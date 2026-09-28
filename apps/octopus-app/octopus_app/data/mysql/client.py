import logging.config
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from logging import Logger, getLogger

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    or_,
)
from sqlalchemy.exc import OperationalError, ProgrammingError

from common.config import MariaDBSettings
from common.exceptions import MariaDBError
from common.mariadb.client import MariaDBClientBase
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import local_day
from octopus_app.data.model import (
    Consumption,
    ConsumptionSummary,
    CostForecast,
    DailyCostSummary,
    Energy,
    as_energy_char,
    energy_from_char,
)
from octopus_app.data.mysql import model
from octopus_app.data.mysql.model import SQLBase
from octopus_app.data.octopus.model import (
    AgileForecastReading,
    Agreement,
    Meter,
    Product,
    Rate,
)

SUMMARIZATION_WINDOW_DAYS = 14

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

# weather_observation/weather_forecast are owned by hive-app, but live in the
# same shared `octopus` MariaDB schema this client already connects to (see
# #511). They're declared here as plain Core Table objects against a
# SEPARATE, unregistered MetaData() instance -- never as ORM classes added to
# mysql/model.py's SQLBase -- so octopus-app's own Schema Sync create_all
# never mistakenly claims ownership of tables hive-app owns (which would risk
# drift/ordering races between the two apps' migrations). Column shapes
# mirror apps/hive-app/hive_app/data/mysql/model.py exactly; kept in sync by
# hand since importing the hive_app package from octopus_app would be a
# cross-app dependency this design deliberately avoids.
weather_metadata = MetaData()

weather_observation_table = Table(
    "weather_observation",
    weather_metadata,
    Column("id", Integer, primary_key=True),
    Column("source", String(20)),
    Column("observed_at", DateTime, nullable=False),
    Column("temp", Float),
    Column("humidity", Float),
    Column("pressure", Float),
    Column("wind_speed", Float),
    Column("precipitation", Float),
    schema="octopus",
)

weather_forecast_table = Table(
    "weather_forecast",
    weather_metadata,
    Column("id", String(50), primary_key=True),
    Column("source", String(20)),
    Column("target_date", Date, nullable=False),
    Column("max_temp", Float),
    Column("fetched_at", DateTime, nullable=False),
    schema="octopus",
)


def _is_missing_table_error(error: Exception) -> bool:
    # hive-app owns weather_observation/weather_forecast and creates them
    # via its own Schema Sync -- if hive-app has never run, these tables
    # simply don't exist yet in the shared database (the documented
    # "hive-app hasn't run yet" scenario in #511's acceptance criteria).
    # MariaDB/PyMySQL raises ProgrammingError with error code 1146 ("table
    # doesn't exist"); SQLite (this app's test fixture) raises
    # OperationalError with a "no such table" message instead -- both are
    # the same logical condition under different backends, so both are
    # checked here rather than assuming one shape. Callers treat this the
    # same as the table existing but having no rows for the query -- an
    # empty result, not a database fault -- and fall back to the
    # flat-average projection method.
    orig = getattr(error, "orig", None)
    if "no such table" in str(orig or error).lower():
        return True
    code = next(iter(getattr(orig, "args", None) or ()), None)
    return code == 1146


@dataclass
class _DailyAccumulator:
    total_kwh: Decimal = Decimal(0)
    variable_cost: Decimal = Decimal(0)
    standing_charge: Decimal = Decimal(0)
    row_count: int = 0


def _energy_scoped_id(energy_char: str, dt: datetime) -> str:
    return energy_char + dt.strftime("%Y%m%d%H%M%S")


def _rate_scoped_id(product_code: str, region: str, valid_from: datetime) -> str:
    return f"{product_code}_{region}_{valid_from.strftime('%Y%m%d%H%M')}"


def _forecast_scoped_id(region: str, period_from: datetime) -> str:
    return f"{region}_{period_from.strftime('%Y%m%d%H%M')}"


class MariaDBClient(MariaDBClientBase):
    def __init__(self, settings: MariaDBSettings) -> None:
        super().__init__(settings, declarative_base=SQLBase, logger=logger)

    def write_consumption(self, meter: Meter, consumption: list[Consumption]) -> None:
        energy_char = as_energy_char(meter.energy)
        records = [
            model.consumption(
                id=_energy_scoped_id(energy_char, point.start),
                energy=energy_char,
                period_from=point.start,
                period_to=point.end,
                raw_value=point.raw,
                unit=point.unit.name,
                est_kwh=point.est_kwh,
            )
            for point in consumption
        ]
        self._write_all(records, "Consumption data")

    def write_agreement(self, meter: Meter, agreements: list[Agreement]) -> None:
        energy_char = as_energy_char(meter.energy)
        records = [
            model.agreement(
                id=_energy_scoped_id(energy_char, agreement.valid_from),
                energy=energy_char,
                product_code=agreement.product_code,
                tariff_code=agreement.tariff_code,
                valid_from=agreement.valid_from,
                valid_to=agreement.valid_to,
            )
            for agreement in agreements
        ]
        self._write_all(records, "Agreement data")

    def write_product(self, product: Product) -> None:
        record = model.product(
            product_code=product.product_code,
            display_name=product.display_name,
            direction=product.direction.value,
        )
        self._write_all([record], "Product data")

    def write_product_rate(
        self, product_code: str, region: str, rates: list[Rate]
    ) -> None:
        records = [
            model.product_rate(
                id=_rate_scoped_id(product_code, region, rate.valid_from),
                product_code=product_code,
                region=region,
                valid_from=rate.valid_from,
                valid_to=rate.valid_to,
                unit_rate=rate.unit_rate,
                standing_charge=rate.standing_charge,
            )
            for rate in rates
        ]
        self._write_all(records, "Product rate data")

    def write_agile_forecast(
        self,
        region: str,
        readings: list[AgileForecastReading],
        fetched_at: datetime,
    ) -> None:
        records = [
            model.agile_forecast(
                id=_forecast_scoped_id(region, reading.period_from),
                region=region,
                period_from=reading.period_from,
                period_to=reading.period_to,
                forecast_unit_rate=reading.unit_rate,
                fetched_at=fetched_at,
            )
            for reading in readings
        ]
        self._write_all(records, "Agile forecast data")

    def write_cost_forecast(self, forecast: CostForecast) -> None:
        record = model.cost_forecast(
            billing_period_start=forecast.billing_period_start,
            billing_period_end=forecast.billing_period_end,
            actual_cost_to_date=forecast.actual_cost_to_date,
            projected_total_cost=forecast.projected_total_cost,
            computed_at=forecast.computed_at,
            energy=as_energy_char(forecast.energy),
        )
        self._write_all([record], "Cost forecast data")

    def read_current_product_rate(
        self, product_code: str, region: str, as_of: datetime
    ) -> Rate | None:
        pr = model.product_rate
        with self.session_read_scope() as session:
            row = (
                session.query(pr)
                .filter(
                    pr.product_code == product_code,
                    pr.region == region,
                    pr.valid_from <= as_of,
                    or_(pr.valid_to.is_(None), as_of < pr.valid_to),
                )
                # Explicit ordering, not bare .first(): if overlapping rows
                # ever matched (bad upstream data), .first() with no ORDER
                # BY is nondeterministic. Most-recently-started wins, same
                # "ORDER BY valid_from DESC LIMIT 1" convention already used
                # for current-rate lookups in data/grafana/mariadb/queries.md.
                .order_by(pr.valid_from.desc())
                .first()
            )
        if row is None:
            return None
        return Rate(
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            unit_rate=row.unit_rate,
            standing_charge=row.standing_charge,
        )

    def read_product_rates_for_local_day(
        self, product_code: str, region: str, day: date
    ) -> list[Rate]:
        # Every product_rate row overlapping the local day -- one row for a
        # standard/fixed tariff (a single rate typically spans the whole
        # agreement), up to ~48 for Agile (one row per half-hour slot). Used
        # to price an estimated gap-filled day at the rate(s) actually
        # published for that specific day, rather than a single instant.
        day_start = local_day.start_of_local_day(day)
        day_end = local_day.start_of_local_day(day + timedelta(days=1))
        pr = model.product_rate
        with self.session_read_scope() as session:
            rows = (
                session.query(pr)
                .filter(
                    pr.product_code == product_code,
                    pr.region == region,
                    pr.valid_from < day_end,
                    or_(pr.valid_to.is_(None), pr.valid_to > day_start),
                )
                .order_by(pr.valid_from)
                .all()
            )
        # DATETIME columns come back tz-naive regardless of backend -- every
        # value stored here is UTC by convention (see read_agile_forecast),
        # reattached here so callers can compare directly against
        # local_day's tz-aware day_start/day_end boundaries.
        return [
            Rate(
                valid_from=row.valid_from.replace(tzinfo=UTC),
                valid_to=row.valid_to.replace(tzinfo=UTC) if row.valid_to else None,
                unit_rate=row.unit_rate,
                standing_charge=row.standing_charge,
            )
            for row in rows
        ]

    def read_agile_forecast(
        self, region: str, as_of: datetime
    ) -> list[AgileForecastReading]:
        af = model.agile_forecast
        with self.session_read_scope() as session:
            rows = (
                session.query(af)
                .filter(af.region == region, af.period_from >= as_of)
                .order_by(af.period_from)
                .all()
            )
        # DATETIME columns come back tz-naive regardless of backend -- every
        # value stored here is UTC by convention (matching local_day's own
        # naive-to-UTC normalization), so it's reattached here rather than
        # left for callers to guess. Without this, the tz-aware `as_of`
        # comparison in _project_agile_variable_cost would raise comparing
        # naive to aware.
        return [
            AgileForecastReading(
                period_from=row.period_from.replace(tzinfo=UTC),
                period_to=row.period_to.replace(tzinfo=UTC),
                unit_rate=row.forecast_unit_rate,
            )
            for row in rows
        ]

    def read_elapsed_billing_period_costs(
        self, period_from: datetime, period_to: datetime, region: str, energy: Energy
    ) -> list[DailyCostSummary]:
        # Joins each half-hourly consumption row to whichever agreement and
        # product_rate actually applied at that moment (not just the
        # current one), so a mid-period rate change is naturally reflected
        # day-by-day. A day with zero consumption rows produces no row here
        # at all -- it's the caller's responsibility to fill that gap, since
        # there's no consumption row to join a standing charge through.
        c = model.consumption
        a = model.agreement
        pr = model.product_rate

        with self.session_read_scope() as session:
            rows = (
                session.query(
                    c.period_from,
                    c.est_kwh.label("est_kwh"),
                    pr.unit_rate.label("unit_rate"),
                    pr.standing_charge.label("standing_charge"),
                )
                .join(
                    a,
                    and_(
                        a.energy == c.energy,
                        c.period_from >= a.valid_from,
                        or_(a.valid_to.is_(None), c.period_from < a.valid_to),
                    ),
                )
                .join(
                    pr,
                    and_(
                        pr.product_code == a.product_code,
                        pr.region == region,
                        c.period_from >= pr.valid_from,
                        or_(pr.valid_to.is_(None), c.period_from < pr.valid_to),
                    ),
                )
                .filter(
                    c.energy == as_energy_char(energy),
                    c.period_from >= period_from,
                    c.period_from < period_to,
                )
                .all()
            )

        # Grouped in Python by Europe/London local calendar day, not the raw
        # UTC date -- Octopus's own daily reporting (and the "day" a UK user
        # means by "cost for 26 July") is local time. See ADR-0010 for why
        # this is Python/zoneinfo-based rather than SQL CONVERT_TZ.
        daily: dict[date, _DailyAccumulator] = {}
        for row in rows:
            day = local_day.to_local_date(row.period_from)
            bucket = daily.setdefault(day, _DailyAccumulator())
            bucket.total_kwh += row.est_kwh
            bucket.variable_cost += row.est_kwh * row.unit_rate
            bucket.standing_charge = max(bucket.standing_charge, row.standing_charge)
            bucket.row_count += 1

        # Octopus's consumption API has a real settlement lag -- a day can
        # still be missing rows more than 24 hours after it ends. A
        # strictly-past day must have all of that local day's expected
        # half-hourly rows (48 normally, 46/50 on a UK clock-change date) to
        # be treated as final; the current/most-recent day (period_to's
        # local date) is exempt since it's expected to be partial by
        # definition ("cost so far"). An incomplete past day drops out
        # entirely here, same as a day with zero consumption rows already
        # does, and is picked up by the caller's gap-fill.
        today = local_day.to_local_date(period_to)
        return [
            DailyCostSummary(
                date=day,
                total_kwh=bucket.total_kwh,
                day_cost_gbp=(bucket.variable_cost + bucket.standing_charge) / 100,
            )
            for day, bucket in daily.items()
            if day == today
            or bucket.row_count == local_day.expected_half_hour_count(day)
        ]

    def read_daily_consumption_summary(
        self, energy: Energy, start_date: date, end_date: date
    ) -> list[ConsumptionSummary]:
        # Inclusive [start_date, end_date] range read straight from the
        # already-summarized daily_consumption_summary table -- unlike
        # read_elapsed_billing_period_costs (which derives daily totals from
        # raw half-hourly consumption rows), this is only ever used for the
        # gas weather-regression's historical training window, which is
        # always well within the summarization job's trailing coverage.
        dcs = model.daily_consumption_summary
        with self.session_read_scope() as session:
            rows = (
                session.query(dcs)
                .filter(
                    dcs.energy == as_energy_char(energy),
                    dcs.date >= start_date,
                    dcs.date <= end_date,
                )
                .all()
            )
        return [
            ConsumptionSummary(energy=energy, date=row.date, total_kwh=row.total_kwh)
            for row in rows
        ]

    def read_weather_observation_daily_max_temps(
        self, start_date: date, end_date: date
    ) -> dict[date, float]:
        # weather_observation has no pre-aggregated daily max (unlike
        # weather_forecast's one-row-per-target_date shape), so the max is
        # taken here in Python across whatever observations exist for each
        # Europe/London local day (ADR-0010) -- not the raw UTC date, to stay
        # consistent with how every other local-day bucketing in this app
        # works. The date range filter below is intentionally generous by a
        # day on each side of the requested window (observations are queried
        # by their real observed_at instant, not yet bucketed) -- correctness
        # comes entirely from the local-day grouping in Python afterward, not
        # from this coarse SQL-side filter.
        window_start = local_day.start_of_local_day(start_date - timedelta(days=1))
        window_end = local_day.start_of_local_day(end_date + timedelta(days=1))
        wo = weather_observation_table
        try:
            with self.session_read_scope() as session:
                rows = (
                    session.query(wo.c.observed_at, wo.c.temp)
                    .filter(
                        wo.c.observed_at >= window_start,
                        wo.c.observed_at < window_end,
                    )
                    .all()
                )
        except (OperationalError, ProgrammingError) as error:
            if not _is_missing_table_error(error):
                raise
            return {}

        daily_max: dict[date, float] = {}
        for observed_at, temp in rows:
            # temp is external, cross-app input (hive-app's weather_observation,
            # read via raw SQL -- see the module-level comment on
            # weather_observation_table) -- a NaN/Infinity row must not
            # silently corrupt the daily max or flow into the gas
            # regression's fit. Treated the same as a missing observation
            # (None), not as an error: hive-app's own weather clients guard
            # against persisting NaN/Infinity today, but this read boundary
            # doesn't assume that holds forever.
            if temp is None or not math.isfinite(temp):
                continue
            day = local_day.to_local_date(observed_at)
            if start_date <= day <= end_date and (
                day not in daily_max or temp > daily_max[day]
            ):
                daily_max[day] = temp
        return daily_max

    def read_weather_forecast_max_temps(
        self, start_date: date, end_date: date
    ) -> dict[date, float]:
        # target_date is already a plain calendar date (one row per forecast
        # day, per hive-app's model) -- no local-day bucketing needed here,
        # unlike weather_observation above.
        wf = weather_forecast_table
        try:
            with self.session_read_scope() as session:
                rows = (
                    session.query(wf.c.target_date, wf.c.max_temp)
                    .filter(
                        wf.c.target_date >= start_date,
                        wf.c.target_date <= end_date,
                    )
                    .all()
                )
        except (OperationalError, ProgrammingError) as error:
            if not _is_missing_table_error(error):
                raise
            return {}
        # Same NaN/Infinity guard as read_weather_observation_daily_max_temps
        # above -- max_temp is external, cross-app input (hive-app's
        # weather_forecast), and a non-finite row must be treated as "no
        # forecast for that day" (the same as a missing row), not silently
        # fed into the gas regression's prediction.
        return {
            target_date: max_temp
            for target_date, max_temp in rows
            if max_temp is not None and math.isfinite(max_temp)
        }

    def read_consumption_summarization_window(
        self, as_of: date
    ) -> list[ConsumptionSummary]:
        # Deliberately no lower bound on the raw `consumption` scan below:
        # gap detection (a day outside the trailing window with no existing
        # summary row) requires seeing all of history, not just the recent
        # cutoff. Table growth is bounded by the raw retention window via
        # the weekly prune_old_data job (see DataPruner), which always runs
        # after this summarization job in the same scheduling tick -- so by
        # the time pruning deletes anything, it has already been summarized.
        # `- 1` because the window is inclusive of as_of itself: 14 trailing
        # days means as_of, as_of-1, ..., as_of-13 (14 dates), not 15.
        cutoff = as_of - timedelta(days=SUMMARIZATION_WINDOW_DAYS - 1)
        with self.session_read_scope() as session:
            raw_rows = session.query(
                model.consumption.energy,
                model.consumption.period_from,
                model.consumption.est_kwh,
            ).all()

            # Grouped in Python by Europe/London local calendar day, not the
            # raw UTC date -- keeps this job's day boundaries consistent
            # with ConsumptionSummaryBackfill, which already buckets by
            # local day (it reads the still-locally-offset Octopus response
            # directly, before any DB round-trip).
            daily_totals: dict[tuple[str, date], Decimal] = {}
            for row in raw_rows:
                day = local_day.to_local_date(row.period_from)
                key = (row.energy, day)
                daily_totals[key] = daily_totals.get(key, Decimal(0)) + row.est_kwh

            # `daily_consumption_summary` is exempt from raw-data pruning
            # (kept for long-running yearly comparisons), so it grows
            # unbounded over years -- restricted to just the dates present
            # in daily_totals (bounded by raw retention) rather than
            # fetching the whole table.
            candidate_days = {day for _, day in daily_totals}
            existing_summary_days: set[tuple[str, date]] = set()
            if candidate_days:
                existing_summary_days = {
                    (row.energy, row.date)
                    for row in session.query(
                        model.daily_consumption_summary.energy,
                        model.daily_consumption_summary.date,
                    )
                    .filter(model.daily_consumption_summary.date.in_(candidate_days))
                    .all()
                }

        return [
            ConsumptionSummary(
                energy=energy_from_char(energy_char),
                date=day,
                total_kwh=total_kwh,
            )
            for (energy_char, day), total_kwh in daily_totals.items()
            if day >= cutoff or (energy_char, day) not in existing_summary_days
        ]

    def write_consumption_summary(self, summaries: list[ConsumptionSummary]) -> None:
        records = [
            model.daily_consumption_summary(
                energy=as_energy_char(summary.energy),
                date=summary.date,
                total_kwh=summary.total_kwh,
            )
            for summary in summaries
        ]
        self._write_all(records, "Consumption summary data")

    def prune_consumption_older_than(self, cutoff: datetime) -> int:
        try:
            with self.session_write_scope() as session:
                deleted = (
                    session.query(model.consumption)
                    .filter(model.consumption.period_from < cutoff)
                    .delete(synchronize_session=False)
                )
                logger.debug(
                    f"Pruned {deleted} consumption row(s) older than {cutoff}."
                )
                return deleted
        except Exception as e:
            logger.error(f"Failed to prune consumption data: {e}")
            raise MariaDBError(e) from e

    def prune_product_rates_older_than(self, cutoff: datetime) -> int:
        try:
            with self.session_write_scope() as session:
                deleted = (
                    session.query(model.product_rate)
                    .filter(
                        model.product_rate.valid_to.isnot(None),
                        model.product_rate.valid_to < cutoff,
                    )
                    .delete(synchronize_session=False)
                )
                logger.debug(
                    f"Pruned {deleted} product_rate row(s) older than {cutoff}."
                )
                return deleted
        except Exception as e:
            logger.error(f"Failed to prune product rate data: {e}")
            raise MariaDBError(e) from e

import logging.config
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import groupby, pairwise
from logging import Logger, getLogger
from typing import NamedTuple, Protocol

from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import local_day
from octopus_app.data.model import (
    ConsumptionSummary,
    CostForecast,
    DailyCostSummary,
    Energy,
)
from octopus_app.data.octopus.model import (
    AgileForecastReading,
    Agreement,
    BillingPeriod,
    MeterSource,
    Rate,
    TariffType,
)

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

TILE_SOURCE_WINDOW_DAYS = 7
HALF_HOURS_PER_DAY = 48

# Bootstrap fallback for a trailing gap day when the current billing period
# has no real day at all yet (see ADR-0023) -- averages the trailing week of
# already-summarized consumption immediately before the period started.
BOOTSTRAP_WINDOW_DAYS = 7

# A few days of upstream rate holes is a blip worth estimating through; more
# than this is a systemic upstream problem whose estimated cost shouldn't be
# silently persisted, so the refresh fails instead. Counts days whose RATES
# were estimated, not every gap-filled day (kWh-only estimates don't count).
MAX_RATE_ESTIMATED_DAYS = 3

# Gas weather regression (#511): a live-computed, nothing-persisted linear
# regression of daily gas kWh against heating degree days, recomputed fresh
# on every refresh() run -- deliberately NOT applied to electricity, which
# keeps the flat average-recent-consumption formula unchanged.
GAS_TRAINING_WINDOW_DAYS = 7
GAS_REGRESSION_MIN_PAIRS = 5
# Below this, the training window's temperatures are treated as effectively
# constant (e.g. a real week of near-identical weather, or a test fixture
# that seeds one flat temperature) -- a regression fit through near-zero
# variance in the input produces a wildly unstable/meaningless slope, so it's
# safer to fall back to the flat average entirely than trust that slope.
# 0.01 only trips on genuinely-degenerate all-identical (or near-identical,
# to float noise) HDD windows, not real-world day-to-day temperature spread.
GAS_REGRESSION_MIN_HDD_VARIANCE = 0.01
# HDD base temperature (Celsius) -- the locked design decision from #511's
# planning session: heating degree days, not raw temperature, is the
# regression's feature, since gas heating demand tracks "how much colder than
# comfortable" rather than absolute temperature.
HDD_BASE_TEMP_C = 15.5


def heating_degree_days(max_temp: float) -> float:
    return max(0.0, HDD_BASE_TEMP_C - max_temp)


@dataclass
class GasWeatherRegression:
    intercept: float
    slope: float

    def predict_daily_kwh(self, max_temp: float) -> float:
        # Floored at zero -- gas consumption can't be negative, but a fitted
        # intercept can be (plausible with noisier real data than any
        # fixture here triggers), which would otherwise silently produce a
        # negative predicted kWh, and so a negative variable cost, for a
        # mild-enough day.
        return max(0.0, self.intercept + self.slope * heating_degree_days(max_temp))


class GasTrainingDay(NamedTuple):
    max_temp: float
    total_kwh: Decimal


def fit_gas_weather_regression(
    pairs: list[GasTrainingDay],
) -> GasWeatherRegression | None:
    """Weighted least-squares fit of daily gas kWh against heating degree
    days, from `pairs` ordered oldest-to-newest.

    Hand-rolled rather than numpy/scikit-learn (per #511's locked design) --
    this is a single-feature closed-form weighted simple linear regression,
    not worth a dependency for. Weights follow a linear recency ramp *by rank
    among the pairs actually passed in* (oldest=1, ..., newest=len(pairs)) --
    not a fixed 7-slot scheme -- so this degrades gracefully whenever fewer
    than a full trailing week of (temp, kWh) pairs are available.

    Returns None (a guard, not an error) when there's too little data or the
    temperatures are too uniform to fit a meaningful slope from -- callers
    fall back to the flat average-consumption projection in that case.
    """
    if len(pairs) < GAS_REGRESSION_MIN_PAIRS:
        return None

    hdds = [heating_degree_days(p.max_temp) for p in pairs]
    kwhs = [float(p.total_kwh) for p in pairs]
    weights = [float(rank) for rank in range(1, len(pairs) + 1)]
    total_weight = sum(weights)

    mean_hdd = sum(w * x for w, x in zip(weights, hdds)) / total_weight
    mean_kwh = sum(w * y for w, y in zip(weights, kwhs)) / total_weight
    variance_hdd = (
        sum(w * (x - mean_hdd) ** 2 for w, x in zip(weights, hdds)) / total_weight
    )
    if variance_hdd < GAS_REGRESSION_MIN_HDD_VARIANCE:
        return None

    covariance = (
        sum(w * (x - mean_hdd) * (y - mean_kwh) for w, x, y in zip(weights, hdds, kwhs))
        / total_weight
    )
    slope = covariance / variance_hdd
    intercept = mean_kwh - slope * mean_hdd
    return GasWeatherRegression(intercept=intercept, slope=slope)


def _decimal_from_float(value: float) -> Decimal:
    # str(), not a bare Decimal(float): float values here (total_seconds(),
    # the gas regression's predicted kWh) carry binary floating-point noise
    # that Decimal(float) would capture verbatim rather than the value's
    # printed (and intended) precision -- immaterial once rounded at the
    # Numeric(9,2) persistence boundary today, but this is a money
    # calculation, so the conversion is done deliberately at this one shared
    # boundary rather than repeated ad hoc at each call site.
    return Decimal(str(value))


def _hours_between(start: datetime, end: datetime) -> Decimal:
    return _decimal_from_float((end - start).total_seconds()) / Decimal(3600)


def project_daily_average_consumption(daily_totals_kwh: list[Decimal]) -> Decimal:
    if not daily_totals_kwh:
        raise ValueError(
            "No elapsed days of consumption to project a future average from."
        )
    return sum(daily_totals_kwh, start=Decimal(0)) / len(daily_totals_kwh)


def tile_forecast_beyond(
    forecast: list[AgileForecastReading], target_end: date
) -> list[AgileForecastReading]:
    if not forecast:
        return []

    by_day = {
        day: list(readings)
        for day, readings in groupby(
            sorted(forecast, key=lambda r: r.period_from),
            key=lambda r: local_day.to_local_date(r.period_from),
        )
    }
    real_days = sorted(by_day.keys())
    last_real_day = real_days[-1]
    if target_end <= last_real_day:
        return []

    source_days = real_days[-TILE_SOURCE_WINDOW_DAYS:]

    tiled: list[AgileForecastReading] = []
    day_offset = 1
    while True:
        synthetic_day = last_real_day + timedelta(days=day_offset)
        source_day = source_days[(day_offset - 1) % len(source_days)]
        shift = synthetic_day - source_day
        for reading in by_day[source_day]:
            tiled.append(
                AgileForecastReading(
                    period_from=reading.period_from + shift,
                    period_to=reading.period_to + shift,
                    unit_rate=reading.unit_rate,
                )
            )
        if synthetic_day >= target_end:
            break
        day_offset += 1

    return tiled


class CostForecastSource(MeterSource, Protocol):
    region_code: str

    def get_current_billing_period(self) -> BillingPeriod: ...

    def read_agile_forecast(
        self, region: str, as_of: datetime
    ) -> list[AgileForecastReading]: ...

    def read_elapsed_billing_period_costs(
        self, period_from: datetime, period_to: datetime, region: str, energy: Energy
    ) -> list[DailyCostSummary]: ...

    def read_current_product_rate(
        self, product_code: str, region: str, as_of: datetime
    ) -> Rate | None: ...

    def read_product_rates_for_local_day(
        self, product_code: str, region: str, day: date
    ) -> list[Rate]: ...

    def read_daily_consumption_summary(
        self, energy: Energy, start_date: date, end_date: date
    ) -> list[ConsumptionSummary]: ...

    def read_weather_observation_daily_max_temps(
        self, start_date: date, end_date: date
    ) -> dict[date, float]: ...

    def read_weather_forecast_max_temps(
        self, start_date: date, end_date: date
    ) -> dict[date, float]: ...

    def persist_cost_forecast(self, forecast: CostForecast) -> None: ...


class CostForecastRetriever:
    _client: CostForecastSource

    def __init__(self, client: CostForecastSource) -> None:
        self._client = client

    def refresh(self, as_of: datetime | None = None) -> None:
        if as_of is None:
            as_of = datetime.now(UTC)

        # Refreshed here (matching ConsumptionRetriever/PricingRetriever's
        # convention) so a newly-added gas meter is picked up on the very
        # next refresh rather than only after a container restart -- without
        # this, self._client.meters could stay stale for the process
        # lifetime and silently suppress the gas forecast below.
        self._client.refresh_meters()
        billing_period = self._client.get_current_billing_period()

        # Electricity remains a hard requirement -- every account has an
        # electricity meter, so a missing one is a genuine error condition
        # (see _current_agreement's RuntimeError). Gas is additive and
        # optional: an electricity-only account simply has no gas meter, so
        # that case is skipped silently below rather than raising.
        self._refresh_for_energy(Energy.electricity, billing_period, as_of)

        if any(m.energy == Energy.gas for m in self._client.meters):
            self._refresh_for_energy(Energy.gas, billing_period, as_of)

    def _refresh_for_energy(
        self, energy: Energy, billing_period: BillingPeriod, as_of: datetime
    ) -> None:
        elapsed_start = local_day.start_of_local_day(billing_period.start)

        # Assumes as_of falls within [billing_period.start, billing_period.
        # end] -- true whenever Kraken's "current" period genuinely contains
        # "now", which is its documented contract. If Kraken's record were
        # ever stale enough that as_of had already passed billing_period.end,
        # this would count a few hours/days belonging to the *next* period
        # into this one; self-correcting once Kraken's own record rolls
        # over, and not guarded against here since it's outside Kraken's
        # documented behavior rather than a case this code can meaningfully
        # detect or correct for.
        agreement = self._current_agreement(energy, as_of)
        daily_costs = self._client.read_elapsed_billing_period_costs(
            elapsed_start, as_of, self._client.region_code, energy
        )
        daily_costs = self._fill_zero_consumption_days(
            energy, billing_period.start, as_of, agreement, daily_costs
        )
        actual_cost_to_date = sum((d.day_cost_gbp for d in daily_costs), Decimal(0))

        remaining_cost = self._project_remaining_cost(
            energy, billing_period, agreement, daily_costs, as_of
        )

        estimated_days = [d.date for d in daily_costs if d.rates_estimated]

        forecast = CostForecast(
            billing_period_start=billing_period.start,
            billing_period_end=billing_period.end,
            actual_cost_to_date=actual_cost_to_date,
            projected_total_cost=actual_cost_to_date + remaining_cost,
            computed_at=as_of,
            energy=energy,
            rates_estimated=bool(estimated_days),
            estimated_days=(
                ",".join(d.isoformat() for d in sorted(estimated_days))
                if estimated_days
                else None
            ),
        )
        self._client.persist_cost_forecast(forecast)
        logger.info(
            f"Cost forecast refresh: {energy.name} billing period "
            f"{billing_period.start}-{billing_period.end}, actual to date "
            f"£{actual_cost_to_date}, projected total "
            f"£{forecast.projected_total_cost}."
        )

    def _current_agreement(self, energy: Energy, as_of: datetime) -> Agreement:
        meter = next((m for m in self._client.meters if m.energy == energy), None)
        if meter is None:
            raise RuntimeError(
                f"No {energy.name} meter found -- cannot compute a cost forecast."
            )
        # "Current" = the agreement whose [valid_from, valid_to) range
        # contains as_of, with valid_to=None treated as unbounded -- not
        # "valid_to is None". Real Agile contracts renew as fixed one-year
        # terms, so Octopus's API never returns valid_to=None for them, not
        # even for the currently-active one; mirrors the range-containment
        # predicate client.py's read_current_product_rate uses instead of
        # requiring an open-ended row. Unlike that query, this doesn't order
        # by valid_from on a tie: Octopus's data model doesn't produce
        # overlapping agreements for one meter, so the first match in
        # response order is taken as-is (see spec for this fix).
        agreement = next(
            (
                a
                for a in meter.agreements
                if a.valid_from <= as_of and (a.valid_to is None or as_of < a.valid_to)
            ),
            None,
        )
        if agreement is None:
            raise RuntimeError(
                f"No current agreement found for the {energy.name} meter as "
                f"of {as_of} -- cannot compute a cost forecast."
            )
        return agreement

    def _fill_zero_consumption_days(
        self,
        energy: Energy,
        billing_period_start: date,
        as_of: datetime,
        agreement: Agreement,
        daily_costs: list[DailyCostSummary],
    ) -> list[DailyCostSummary]:
        # A day with zero consumption rows, or a strictly-past day still
        # missing some of its half-hourly rows (settlement lag), produces no
        # row from the join in read_elapsed_billing_period_costs -- there's
        # no consumption row to join a rate through. Rather than reporting
        # standing-charge-only for that day (see ADR-0023), an estimated
        # variable cost is priced in too: interpolated from real
        # neighbouring days when data has already resumed after the gap
        # ("interior"), or projected from an average when it hasn't
        # ("trailing") -- see _estimate_gap_day_kwh. One query per missing
        # day (accepted tradeoff: a billing period is at most ~31 days and
        # gap days are rare, so this never approaches a scale where batching
        # the lookup would be worth the complexity).
        present_day_set = {d.date for d in daily_costs}
        # as_of's own local date is exempt from the day-completeness guard
        # (see read_elapsed_billing_period_costs) precisely because it's
        # still arriving -- a real but partial row, not a finished day's
        # total. present_day_set still includes it (so it's never itself
        # re-estimated as a gap), but it's excluded from the candidates
        # _estimate_gap_day_kwh averages or interpolates OTHER gap days
        # from, or its partial-so-far total would understate them. Its own
        # actual (partial) cost is untouched -- still in daily_costs/filled.
        as_of_local_date = local_day.to_local_date(as_of)
        average_candidates = [d for d in daily_costs if d.date != as_of_local_date]
        present_days = sorted(d.date for d in average_candidates)
        kwh_by_present_day = {d.date: d.total_kwh for d in average_candidates}
        filled = list(daily_costs)
        day = billing_period_start
        while local_day.start_of_local_day(day) < as_of:
            if day not in present_day_set:
                # as_of's own local date, when it has no consumption rows at
                # all (as opposed to a real-but-partial row, the case
                # excluded above), is still priced standing-charge-only
                # here -- never with an estimated variable cost.
                # _project_remaining_cost's remaining_hours already spans
                # from as_of through the rest of the billing period, which
                # for a same-day gap already covers the whole of today's
                # not-yet-metered variable cost; estimating it again here
                # would double-count it (see _remaining_billing_window's
                # "remaining_hours ... includes the rest of today" comment
                # -- that logic assumes a same-day daily_costs row reflects
                # only what's actually been metered so far, which a full
                # variable-cost estimate here would violate).
                daily_kwh = (
                    None
                    if day == as_of_local_date
                    else self._estimate_gap_day_kwh(
                        energy,
                        day,
                        present_days,
                        kwh_by_present_day,
                        billing_period_start,
                    )
                )
                filled.append(self._price_gap_day(agreement, day, daily_kwh))
                if sum(d.rates_estimated for d in filled) > MAX_RATE_ESTIMATED_DAYS:
                    raise RuntimeError(
                        f"More than {MAX_RATE_ESTIMATED_DAYS} days need "
                        f"their {agreement.product_code} rates estimated "
                        f"(the cap is at most {MAX_RATE_ESTIMATED_DAYS} "
                        f"per refresh; reached {day}) -- refusing to "
                        "persist a cost forecast this dependent on "
                        "estimated rates."
                    )
            day += timedelta(days=1)
        return filled

    def _estimate_gap_day_kwh(
        self,
        energy: Energy,
        day: date,
        present_days: list[date],
        kwh_by_present_day: dict[date, Decimal],
        billing_period_start: date,
    ) -> Decimal | None:
        before = next((d for d in reversed(present_days) if d < day), None)
        after = next((d for d in present_days if d > day), None)

        if after is not None:
            # Interior gap: a later real day already exists in this period,
            # so data has resumed -- interpolate from the real neighbour(s)
            # either side. No earlier neighbour (this gap sits before any
            # real day has arrived yet) simply means interpolating from the
            # after-day alone.
            neighbours = [kwh_by_present_day[d] for d in (before, after) if d]
            return project_daily_average_consumption(neighbours)

        if present_days:
            # Trailing gap: nothing has settled after this day yet, but this
            # billing period has at least one real day -- use the same
            # period average the remaining-days projection uses.
            return project_daily_average_consumption(
                [kwh_by_present_day[d] for d in present_days]
            )

        # Bootstrap: not a single real day anywhere in this period yet (e.g.
        # day one, run before Octopus has returned any consumption). Falls
        # back to the trailing week immediately before the period started;
        # None (ultimate fallback to standing-charge-only) only when that
        # history doesn't exist either, e.g. a brand-new account.
        return self._bootstrap_daily_kwh_average(energy, billing_period_start)

    def _bootstrap_daily_kwh_average(
        self, energy: Energy, billing_period_start: date
    ) -> Decimal | None:
        window_end = billing_period_start - timedelta(days=1)
        window_start = window_end - timedelta(days=BOOTSTRAP_WINDOW_DAYS - 1)
        summaries = self._client.read_daily_consumption_summary(
            energy, window_start, window_end
        )
        if not summaries:
            return None
        return project_daily_average_consumption([s.total_kwh for s in summaries])

    def _price_gap_day(
        self, agreement: Agreement, day: date, daily_kwh: Decimal | None
    ) -> DailyCostSummary:
        rates = self._client.read_product_rates_for_local_day(
            agreement.product_code, self._client.region_code, day
        )
        day_start = local_day.start_of_local_day(day)
        day_end = local_day.start_of_local_day(day + timedelta(days=1))
        # Rates can genuinely overlap (a documented, if rare, upstream data
        # anomaly) -- rather than resolving that two different ways (dedupe
        # for the standing charge, sum-everything for the variable cost,
        # silently double-billing the overlapping window), the day is
        # partitioned up front into non-overlapping segments, each already
        # resolved to its single winning rate. Both the standing charge and
        # the variable cost read from this one partition, so they can never
        # again disagree about which rate covers which instant.
        segments = self._day_segments(rates, day_start, day_end)
        # Standing-charge-only day (today): only the rate at "midday" (12
        # hours after the local day starts -- the same instant
        # _midday_standing_charge reads, 13:00 local on a spring-forward
        # day) is used, so only that needs to be published. Agile publishes
        # to 23:00 UK local, an hour short of local midnight in BST.
        if daily_kwh is None and not self._covers_midday(segments, day_start):
            raise RuntimeError(
                f"No product_rate found for {agreement.product_code} "
                f"on {day} -- the local-midday rate is missing, so "
                "that day's standing charge cannot be computed for "
                "actual_cost_to_date."
            )
        rates_estimated = False
        if daily_kwh is not None and not self._segments_fully_cover_day(
            segments, day_start, day_end
        ):
            # A past day needing a variable cost with an upstream rate hole
            # is estimated rather than failing the whole refresh. A day with
            # no published segment, or none covering local midday (so no
            # standing charge), is still an error.
            if not self._covers_midday(segments, day_start):
                raise RuntimeError(
                    f"No product_rate found for {agreement.product_code} "
                    f"on {day} -- cannot compute actual_cost_to_date "
                    "without silently omitting that day's standing charge or "
                    "estimated variable cost."
                )
            rates_estimated = True

        standing_charge = self._midday_standing_charge(segments, day_start)
        variable_cost = self._segments_variable_cost(segments, daily_kwh)
        if rates_estimated and daily_kwh is not None:
            variable_cost += self._uncovered_variable_cost(
                segments, daily_kwh, day_start, day_end
            )

        return DailyCostSummary(
            date=day,
            total_kwh=daily_kwh if daily_kwh is not None else Decimal(0),
            day_cost_gbp=(variable_cost + standing_charge) / 100,
            is_gap_filled=True,
            rates_estimated=rates_estimated,
        )

    @staticmethod
    def _covers_midday(
        segments: list[tuple[datetime, datetime, Rate]], day_start: datetime
    ) -> bool:
        # Midday is 12 hours after the local day starts (13:00 local on a
        # spring-forward day) -- the instant the standing charge is read at.
        midday = day_start + timedelta(hours=12)
        return any(start <= midday < end for start, end, _ in segments)

    @staticmethod
    def _uncovered_variable_cost(
        segments: list[tuple[datetime, datetime, Rate]],
        daily_kwh: Decimal,
        day_start: datetime,
        day_end: datetime,
    ) -> Decimal:
        # Uncovered stretches are priced at the time-weighted average unit
        # rate of the day's published segments (weight = duration), so the
        # hole neither inflates nor deflates the day's cost.
        published_hours = sum(
            (_hours_between(start, end) for start, end, _ in segments), Decimal(0)
        )
        average_rate = (
            sum(
                (
                    _hours_between(start, end) * rate.unit_rate
                    for start, end, rate in segments
                ),
                Decimal(0),
            )
            / published_hours
        )
        uncovered_hours = _hours_between(day_start, day_end) - published_hours
        return (uncovered_hours / 24) * daily_kwh * average_rate

    @staticmethod
    def _midday_standing_charge(
        segments: list[tuple[datetime, datetime, Rate]], day_start: datetime
    ) -> Decimal:
        # Standing charge is a flat per-day fee, not prorated (see
        # _remaining_billing_window) -- so on the rare day a tariff renewal
        # changes it mid-day, the segment covering local midday supplies the
        # day's single charge, matching the pre-existing midday-lookup
        # convention this replaced, rather than max() across every rate
        # touching the day (which would pick whichever happens to be larger,
        # an arbitrary and unreviewed choice for a money calculation). No
        # fallback needed on the lookup below: the caller has verified that
        # a segment covers local midday (the standing-charge-only path
        # checks midday coverage alone; days needing a variable cost check
        # full-day coverage, which implies it). Segments never overlap, so
        # exactly one of them contains midday.
        midday = day_start + timedelta(hours=12)
        _, _, standing_rate = next(
            segment for segment in segments if segment[0] <= midday < segment[1]
        )
        return standing_rate.standing_charge

    @staticmethod
    def _segments_variable_cost(
        segments: list[tuple[datetime, datetime, Rate]], daily_kwh: Decimal | None
    ) -> Decimal:
        if daily_kwh is None:
            return Decimal(0)
        variable_cost = Decimal(0)
        for segment_start, segment_end, rate in segments:
            segment_hours = _hours_between(segment_start, segment_end)
            variable_cost += (segment_hours / 24) * daily_kwh * rate.unit_rate
        return variable_cost

    @staticmethod
    def _day_overlap(
        rate: Rate, day_start: datetime, day_end: datetime
    ) -> tuple[datetime, datetime] | None:
        overlap_start = max(rate.valid_from, day_start)
        overlap_end = min(rate.valid_to or day_end, day_end)
        if overlap_end <= overlap_start:
            return None
        return overlap_start, overlap_end

    @classmethod
    def _day_segments(
        cls, rates: list[Rate], day_start: datetime, day_end: datetime
    ) -> list[tuple[datetime, datetime, Rate]]:
        # Partitions [day_start, day_end) into non-overlapping segments, each
        # assigned a single winning rate -- the one with the latest
        # valid_from among every rate whose (day-clipped) window fully
        # spans that segment ("most-recently-started wins", the same
        # tiebreak read_current_product_rate already uses for its own
        # overlapping-row lookup). A stretch no rate's window fully spans
        # produces no segment at all, surfacing as a gap for the callers'
        # coverage checks to catch (_segments_fully_cover_day, or the
        # midday-only check for a standing-charge-only day).
        clipped = [
            (overlap[0], overlap[1], rate)
            for rate in rates
            if (overlap := cls._day_overlap(rate, day_start, day_end)) is not None
        ]
        boundaries = sorted({day_start, day_end, *(b for c in clipped for b in c[:2])})
        segments: list[tuple[datetime, datetime, Rate]] = []
        for segment_start, segment_end in pairwise(boundaries):
            covering = [
                c for c in clipped if c[0] <= segment_start and segment_end <= c[1]
            ]
            if not covering:
                continue
            winner = max(covering, key=lambda c: c[2].valid_from)
            segments.append((segment_start, segment_end, winner[2]))
        return segments

    @staticmethod
    def _segments_fully_cover_day(
        segments: list[tuple[datetime, datetime, Rate]],
        day_start: datetime,
        day_end: datetime,
    ) -> bool:
        if not segments or segments[0][0] != day_start or segments[-1][1] != day_end:
            return False
        return all(
            segments[i][1] == segments[i + 1][0] for i in range(len(segments) - 1)
        )

    def _remaining_billing_window(
        self,
        billing_period: BillingPeriod,
        daily_costs: list[DailyCostSummary],
        as_of: datetime,
    ) -> tuple[int, Decimal] | None:
        # billing_period.end is treated as the last billable day
        # (inclusive), not the first day of the next period -- Kraken
        # exposes a separate nextBillingDate field distinct from
        # currentBillingPeriodEndDate, which would be redundant if the end
        # date were exclusive. remaining_days (whole future days, for
        # standing charge only) is derived as "total period days minus days
        # already accounted for in daily_costs" rather than a raw
        # (end - as_of.date()) subtraction: the latter silently drops
        # as_of.date() ("today") from *both* the elapsed and remaining
        # counts whenever as_of lands on an exact midnight.
        total_period_days = (billing_period.end - billing_period.start).days + 1
        remaining_days = total_period_days - len(daily_costs)

        # remaining_hours spans from as_of through the end of the inclusive
        # billing_period_end -- unlike remaining_days, this correctly
        # includes the *rest of today* whenever as_of has already been
        # counted as an elapsed day (i.e. whenever as_of isn't exactly
        # midnight, the normal production case since the daily job runs at
        # DAILY_JOB_TIME = "04:00"). Today's standing charge is already
        # fully covered by the elapsed-days query/gap-fill above (a flat
        # per-day fee, not prorated), so remaining_days alone is correct for
        # standing_cost -- but the *variable* (unit-rate) cost for today's
        # not-yet-metered remaining hours would otherwise be silently
        # dropped every single day, since Octopus consumption data lags and
        # "today" frequently has no rows yet by the time the job runs.
        period_end_boundary = local_day.start_of_local_day(
            billing_period.end + timedelta(days=1)
        )
        remaining_hours = _hours_between(as_of, period_end_boundary)
        if remaining_hours <= 0:
            return None
        return remaining_days, remaining_hours

    def _project_remaining_cost(
        self,
        energy: Energy,
        billing_period: BillingPeriod,
        agreement: Agreement,
        daily_costs: list[DailyCostSummary],
        as_of: datetime,
    ) -> Decimal:
        window = self._remaining_billing_window(billing_period, daily_costs, as_of)
        if window is None:
            return Decimal(0)
        remaining_days, remaining_hours = window

        # Gap-filled days (zero-consumption or a still-arriving, incomplete
        # day) don't represent real observed usage -- letting them count as
        # zero-kWh days here would drag the projection down every time a
        # recent day hasn't fully settled yet, which per the observed
        # settlement lag is common, not rare. as_of's own local date is
        # excluded too, for the same reason _estimate_gap_day_kwh excludes
        # it: read_elapsed_billing_period_costs exempts it from the
        # completeness guard precisely because it's still partial, so it
        # would otherwise silently understate this average every single
        # refresh (the daily job always runs mid-day). Falls back to the
        # full (unfiltered, today included) list on the rare
        # day-one-of-a-billing-period case where every elapsed day so far
        # is gap-filled or still-partial -- there's no complete day to
        # prefer yet, and an empty list would raise below rather than
        # produce a (rough, self-correcting) forecast.
        as_of_local_date = local_day.to_local_date(as_of)
        real_daily_totals = [
            d.total_kwh
            for d in daily_costs
            if not d.is_gap_filled and d.date != as_of_local_date
        ]
        future_daily_kwh = project_daily_average_consumption(
            real_daily_totals or [d.total_kwh for d in daily_costs]
        )
        current_rate = self._client.read_current_product_rate(
            agreement.product_code, self._client.region_code, as_of
        )
        if current_rate is None:
            raise RuntimeError(
                f"No product_rate found for {agreement.product_code} in "
                f"{self._client.region_code} as of {as_of} -- cannot "
                "project remaining billing period cost."
            )

        standing_cost = max(remaining_days, 0) * current_rate.standing_charge

        if agreement.tariff_type == TariffType.agile:
            variable_cost = self._project_agile_variable_cost(
                billing_period.end, future_daily_kwh, as_of
            )
        elif energy == Energy.gas:
            # Gas only (#511) -- electricity keeps the flat scalar formula
            # below completely unchanged. This replaces that single-scalar
            # calculation with a per-remaining-day loop so each day can use
            # either a weather-regression prediction or the flat average,
            # depending on forecast data availability for that specific day.
            variable_cost = self._project_gas_variable_cost(
                billing_period.end, as_of, future_daily_kwh, current_rate
            )
        else:
            variable_cost = (
                (remaining_hours / 24) * future_daily_kwh * current_rate.unit_rate
            )

        return (variable_cost + standing_cost) / 100

    def _project_agile_variable_cost(
        self, billing_period_end: date, future_daily_kwh: Decimal, as_of: datetime
    ) -> Decimal:
        # Reads whatever the hourly Agile Forecast Refresh job most recently
        # persisted, rather than fetching agilepredict.com/x2r.uk live here --
        # that job is the sole writer of agile_forecast (including the data
        # the pre-existing "Price Curve" Grafana panel reads), so an outage of
        # either forecast source no longer fails this daily cost projection.
        forecast_readings = self._client.read_agile_forecast(
            self._client.region_code, as_of
        )
        # No readings at all (not merely stale ones -- that case is accepted,
        # see the spec) means tile_forecast_beyond has nothing to tile from
        # either, silently projecting zero variable cost for the rest of the
        # period. Raising here restores the guarantee the old live-fetch path
        # gave for free (AgilePredictClient.get_forecast raised APIError on
        # an empty response) and matches this method's own established
        # "raise rather than guess" convention for money calculations (see
        # the current_rate check below).
        if not forecast_readings:
            raise RuntimeError(
                f"No Agile forecast data found for region "
                f"{self._client.region_code} from {as_of} onward -- cannot "
                "project remaining billing period cost."
            )
        # +1 day: billing_period_end is the last inclusive billable day, so
        # the window must extend through its own half-hourly slots, not
        # stop at its midnight boundary (which would exclude the entire
        # final day from the variable-cost sum while still charging its
        # standing fee via remaining_days).
        end_datetime = local_day.start_of_local_day(
            billing_period_end + timedelta(days=1)
        )
        tiled = tile_forecast_beyond(forecast_readings, billing_period_end)
        remaining_readings = [
            r
            for r in forecast_readings + tiled
            if as_of <= r.period_from < end_datetime
        ]
        per_slot_kwh = future_daily_kwh / HALF_HOURS_PER_DAY
        return sum((per_slot_kwh * r.unit_rate for r in remaining_readings), Decimal(0))

    def _fit_gas_weather_regression(
        self, as_of: datetime
    ) -> GasWeatherRegression | None:
        # Trailing 7-day window ending the day *before* as_of's local date
        # (ADR-0010, Europe/London not UTC) -- as_of's own local day is
        # excluded since that day's consumption is still arriving/partial,
        # not a settled historical data point to train on.
        as_of_local_date = local_day.to_local_date(as_of)
        training_end = as_of_local_date - timedelta(days=1)
        training_start = training_end - timedelta(days=GAS_TRAINING_WINDOW_DAYS - 1)

        consumption = self._client.read_daily_consumption_summary(
            Energy.gas, training_start, training_end
        )
        observed_max_temps = self._client.read_weather_observation_daily_max_temps(
            training_start, training_end
        )
        kwh_by_date = {c.date: c.total_kwh for c in consumption}

        # A day missing either side of the pair (no consumption summary row,
        # or no weather observation for that local day) is simply excluded
        # from the training set -- not an error, per #511's locked design.
        # Sorted ascending so fit_gas_weather_regression's rank-based
        # recency weighting assigns the oldest available pair weight 1.
        pairs = [
            GasTrainingDay(observed_max_temps[d], kwh_by_date[d])
            for d in sorted(kwh_by_date)
            if d in observed_max_temps
        ]
        regression = fit_gas_weather_regression(pairs)
        # Logged here (not just left to fall silently into the flat-average
        # path) so an operator can tell from job_run/logs alone whether
        # today's gas projection is currently weather-driven or has quietly
        # degraded to the average method -- e.g. hive-app stalled, or a
        # genuinely flat weather week guard-skipped the fit.
        if regression is None:
            logger.info(
                f"Gas weather regression skipped ({len(pairs)} training "
                f"pair(s) available, window {training_start}-{training_end}) "
                "-- falling back to the average-consumption method for the "
                "whole remaining period."
            )
        else:
            logger.info(
                f"Gas weather regression fit from {len(pairs)} training "
                f"pair(s) (window {training_start}-{training_end}): "
                f"intercept={regression.intercept:.3f}, "
                f"slope={regression.slope:.3f}."
            )
        return regression

    def _hours_remaining_in_day(self, as_of: datetime, day: date) -> Decimal:
        # Uses the same _hours_between helper as _remaining_billing_window,
        # scoped to just this one day -- as_of's own local day gets the
        # fractional remainder from as_of to local midnight; every later day
        # gets a full 24 hours. max() guards against as_of landing after
        # day_start on a day that isn't today (never happens given this
        # method's only caller, but keeps the arithmetic honest either way).
        day_start = local_day.start_of_local_day(day)
        day_end = local_day.start_of_local_day(day + timedelta(days=1))
        return _hours_between(max(as_of, day_start), day_end)

    def _gas_day_kwh(
        self,
        regression: GasWeatherRegression | None,
        forecast_max_temps: dict[date, float],
        day: date,
        future_daily_kwh: Decimal,
    ) -> Decimal:
        max_temp = forecast_max_temps.get(day)
        if regression is not None and max_temp is not None:
            return _decimal_from_float(regression.predict_daily_kwh(max_temp))
        # No forecast row for this specific day, or the regression was
        # guard-skipped for the whole period -- either way, this day falls
        # back to the same flat average every other (non-gas, non-Agile)
        # projection already uses.
        return future_daily_kwh

    def _project_gas_variable_cost(
        self,
        billing_period_end: date,
        as_of: datetime,
        future_daily_kwh: Decimal,
        current_rate: Rate,
    ) -> Decimal:
        # The regression is fit (or guard-skipped) once for the whole
        # remaining period, not per day -- a guard failure means the flat
        # average is used for every remaining day, never a per-day retry.
        regression = self._fit_gas_weather_regression(as_of)

        as_of_local_date = local_day.to_local_date(as_of)
        forecast_max_temps = self._client.read_weather_forecast_max_temps(
            as_of_local_date, billing_period_end
        )

        variable_cost = Decimal(0)
        regression_day_count = 0
        day = as_of_local_date
        while day <= billing_period_end:
            hours_remaining_in_day = self._hours_remaining_in_day(as_of, day)
            day_kwh = self._gas_day_kwh(
                regression, forecast_max_temps, day, future_daily_kwh
            )
            if regression is not None and day in forecast_max_temps:
                regression_day_count += 1
            variable_cost += (
                (hours_remaining_in_day / 24) * day_kwh * current_rate.unit_rate
            )
            day += timedelta(days=1)

        # One summary line per refresh, not per day -- a per-day log for a
        # ~31-day billing period would be noise, but an operator still needs
        # to see, at a glance, how many of the remaining days were actually
        # weather-driven versus quietly using the flat-average fallback
        # (e.g. Open-Meteo's forecast horizon doesn't reach the whole
        # billing period, or hive-app has been down).
        total_remaining_days = (billing_period_end - as_of_local_date).days + 1
        logger.info(
            f"Gas variable cost: {regression_day_count}/{total_remaining_days} "
            "remaining day(s) used the weather regression, the rest used the "
            "flat average."
        )
        return variable_cost

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import responses
from sqlalchemy import text
from sqlalchemy.orm import Session
from tests.weather_fixtures import TRAINING_DAYS_AND_TEMPS as _TRAINING_DAYS_AND_TEMPS
from tests.weather_fixtures import mock_billing_period as _mock_billing_period
from tests.weather_fixtures import (
    seed_daily_consumption_summary as _seed_daily_consumption_summary,
)
from tests.weather_fixtures import seed_weather_forecast as _seed_weather_forecast
from tests.weather_fixtures import seed_weather_observation as _seed_weather_observation

from octopus_app.common.config import OctopusAPISettings
from octopus_app.data.cost_forecast import (
    CostForecastRetriever,
    GasTrainingDay,
    fit_gas_weather_regression,
)
from octopus_app.data.local_day import start_of_local_day
from octopus_app.data.model import (
    ConsumptionSummary,
    CostForecast,
    DailyCostSummary,
    Energy,
)
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.octopus.kraken import BillingPeriodClient, KrakenTransport
from octopus_app.data.octopus.model import (
    AgileForecastReading,
    Agreement,
    BillingPeriod,
    Electricity,
    Gas,
    Meter,
    Rate,
)

GRAPHQL_ENDPOINT = "https://api.octopus.energy/v1/graphql/"
PRODUCT_CODE = "VAR-24-10-01"
GAS_PRODUCT_CODE = "VAR-22-11-01"
REGION = "H"


class _RealCostForecastSource:
    """Real MariaDBClient/BillingPeriodClient underneath -- HTTP calls
    mocked via `responses`, DB is the real SQLite fixture -- with meters
    fixed up front so tests don't need to mock the account meter-information
    endpoint too."""

    def __init__(
        self,
        mariadb: MariaDBClient,
        billing_period_client: BillingPeriodClient,
        meters: list[Meter],
        region_code: str,
    ) -> None:
        self._mariadb = mariadb
        self._billing_period_client = billing_period_client
        self.meters = meters
        self.region_code = region_code

    def refresh_meters(self) -> None:
        pass

    def get_current_billing_period(self) -> BillingPeriod:
        return self._billing_period_client.get_current_billing_period()

    def read_agile_forecast(
        self, region: str, as_of: datetime
    ) -> list[AgileForecastReading]:
        return self._mariadb.read_agile_forecast(region, as_of)

    def read_elapsed_billing_period_costs(
        self, period_from: datetime, period_to: datetime, region: str, energy: Energy
    ) -> list[DailyCostSummary]:
        return self._mariadb.read_elapsed_billing_period_costs(
            period_from, period_to, region, energy
        )

    def read_current_product_rate(
        self, product_code: str, region: str, as_of: datetime
    ) -> Rate | None:
        return self._mariadb.read_current_product_rate(product_code, region, as_of)

    def read_daily_consumption_summary(
        self, energy: Energy, start_date: date, end_date: date
    ) -> list[ConsumptionSummary]:
        return self._mariadb.read_daily_consumption_summary(
            energy, start_date, end_date
        )

    def read_weather_observation_daily_max_temps(
        self, start_date: date, end_date: date
    ) -> dict[date, float]:
        return self._mariadb.read_weather_observation_daily_max_temps(
            start_date, end_date
        )

    def read_weather_forecast_max_temps(
        self, start_date: date, end_date: date
    ) -> dict[date, float]:
        return self._mariadb.read_weather_forecast_max_temps(start_date, end_date)

    def persist_cost_forecast(self, forecast: CostForecast) -> None:
        self._mariadb.write_cost_forecast(forecast)


class _MeterDiscoveringCostForecastSource(_RealCostForecastSource):
    """Simulates a gas meter that only becomes visible after
    `refresh_meters()` is called -- e.g. newly added to the account after
    `CostForecastRetriever` was constructed -- so a test can prove
    `refresh_meters()` is actually wired into `refresh()`, not merely
    present as a no-op. Without that call, `self.meters` would stay at
    `initial_meters` (electricity only) for the process lifetime."""

    def __init__(
        self,
        mariadb: MariaDBClient,
        billing_period_client: BillingPeriodClient,
        initial_meters: list[Meter],
        discovered_meters: list[Meter],
        region_code: str,
    ) -> None:
        super().__init__(mariadb, billing_period_client, initial_meters, region_code)
        self._discovered_meters = discovered_meters

    def refresh_meters(self) -> None:
        self.meters = self._discovered_meters


def _seed_complete_day(
    s: Session, day: date, est_kwh_per_slot: str, energy: str = "E"
) -> None:
    # A full 48-slot local day -- the completeness guard requires this for
    # any strictly-past elapsed day to count as real, priced consumption
    # rather than falling through to the zero-consumption gap-fill.
    start = start_of_local_day(day)
    for slot in range(48):
        slot_start = start + timedelta(minutes=30 * slot)
        s.add(
            model.consumption(
                id=f"{energy}{slot_start.strftime('%Y%m%d%H%M%S')}",
                energy=energy,
                period_from=slot_start,
                period_to=slot_start + timedelta(minutes=30),
                raw_value=Decimal(est_kwh_per_slot),
                unit="kWh",
                est_kwh=Decimal(est_kwh_per_slot),
            )
        )


def _make_electricity_meter(
    tariff_code: str = f"E-1R-{PRODUCT_CODE}-{REGION}",
    valid_from: datetime = datetime(2022, 1, 1, tzinfo=UTC),
    valid_to: datetime | None = None,
) -> Electricity:
    return Electricity(
        mpan="1234567890123",
        serial_number="00A1234567",
        agreements=[
            Agreement(tariff_code=tariff_code, valid_from=valid_from, valid_to=valid_to)
        ],
    )


def _make_gas_meter(
    tariff_code: str = f"G-1R-{GAS_PRODUCT_CODE}-{REGION}",
    valid_from: datetime = datetime(2022, 1, 1, tzinfo=UTC),
    valid_to: datetime | None = None,
) -> Gas:
    return Gas(
        mprn="1234567890",
        serial_number="G00A123456",
        agreements=[
            Agreement(tariff_code=tariff_code, valid_from=valid_from, valid_to=valid_to)
        ],
    )


def _source(mariadb: MariaDBClient, meters: list[Meter]) -> _RealCostForecastSource:
    settings = OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
    return _RealCostForecastSource(
        mariadb,
        BillingPeriodClient(settings, KrakenTransport()),
        meters,
        REGION,
    )


def _seed_electricity_and_gas_fixtures(s: Session) -> None:
    s.add(
        model.agreement(
            id="E20220101000000",
            energy="E",
            product_code=PRODUCT_CODE,
            tariff_code=f"E-1R-{PRODUCT_CODE}-{REGION}",
            valid_from=datetime(2022, 1, 1, tzinfo=UTC),
            valid_to=None,
        )
    )
    s.add(
        model.product_rate(
            id=f"{PRODUCT_CODE}_{REGION}_202601010000",
            product_code=PRODUCT_CODE,
            region=REGION,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            valid_to=None,
            unit_rate=Decimal("20.00"),
            standing_charge=Decimal("48.00"),
        )
    )
    _seed_complete_day(s, date(2026, 7, 6), "0.1", energy="E")

    s.add(
        model.agreement(
            id="G20220101000000",
            energy="G",
            product_code=GAS_PRODUCT_CODE,
            tariff_code=f"G-1R-{GAS_PRODUCT_CODE}-{REGION}",
            valid_from=datetime(2022, 1, 1, tzinfo=UTC),
            valid_to=None,
        )
    )
    s.add(
        model.product_rate(
            id=f"{GAS_PRODUCT_CODE}_{REGION}_202601010000",
            product_code=GAS_PRODUCT_CODE,
            region=REGION,
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            valid_to=None,
            unit_rate=Decimal("7.00"),
            standing_charge=Decimal("29.00"),
        )
    )
    # One elapsed day (2026-07-06), a full 48-slot day totalling 48.0 kWh.
    _seed_complete_day(s, date(2026, 7, 6), "1.0", energy="G")


@responses.activate
def test_gas_actual_cost_is_computed_and_written_as_its_own_energy_row(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")

    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        gas_row = session.query(model.cost_forecast).filter_by(energy="G").one()
        electricity_row = session.query(model.cost_forecast).filter_by(energy="E").one()

    # 48 slots * 1.0 kWh/slot = 48.0 kWh @ 7.00p + 29.00p standing =
    # 336.00p + 29.00p = 365.00p -> £3.65
    assert gas_row.actual_cost_to_date == Decimal("3.65")
    # (4.8 kWh @ 20.00p) + 48.00p standing charge = 144.00p -> £1.44,
    # unaffected by gas now also being computed.
    assert electricity_row.actual_cost_to_date == Decimal("1.44")


@responses.activate
def test_gas_projected_total_cost_uses_the_same_average_consumption_formula_as_electricity(
    mariadb_client: MariaDBClient,
) -> None:
    # Gas has no Agile tariff, so its projection must go through the exact
    # same average-recent-consumption, non-Agile formula electricity already
    # uses -- mirrors test_fixed_tariff_actual_cost_and_projection's
    # arithmetic (in test_cost_forecast_retriever.py), but for a gas
    # meter/tariff. Electricity fixtures are the minimal shape needed to
    # satisfy refresh()'s hard electricity requirement; the assertions below
    # are all about the gas row.
    _mock_billing_period("2026-07-07", "2026-08-07")

    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        gas_row = session.query(model.cost_forecast).filter_by(energy="G").one()

    # (48.0 kWh @ 7.00p) + 29.00p standing charge = 365.00p -> £3.65
    assert gas_row.actual_cost_to_date == Decimal("3.65")
    # total_period_days = Jul6..Aug6 inclusive = 32; remaining_days = 32 - 1
    # elapsed day (Jul6) = 31, at 48.0 kWh/day average, same 7.00p rate +
    # 29.00p standing charge/day -- identical formula to the electricity
    # case in test_fixed_tariff_actual_cost_and_projection.
    remaining_days = 31
    expected_remaining = (
        remaining_days * (Decimal("48.0") * Decimal("7.00") + Decimal("29.00")) / 100
    )
    assert (
        gas_row.projected_total_cost == gas_row.actual_cost_to_date + expected_remaining
    )


@responses.activate
def test_a_gas_meter_added_after_construction_is_picked_up_via_refresh_meters(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")

    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)

    settings = OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
    source = _MeterDiscoveringCostForecastSource(
        mariadb_client,
        BillingPeriodClient(settings, KrakenTransport()),
        initial_meters=[_make_electricity_meter()],
        discovered_meters=[_make_electricity_meter(), _make_gas_meter()],
        region_code=REGION,
    )

    retriever = CostForecastRetriever(source)
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        energies = {row.energy for row in session.query(model.cost_forecast).all()}

    # If refresh() failed to call refresh_meters() first, self.meters would
    # stay at initial_meters (electricity only) and this would be {"E"}.
    assert energies == {"E", "G"}


# Billing period (after Kraken's date-shift) runs 2026-07-06 .. 2026-08-06;
# every remaining day from as_of's local date (2026-07-07) through the
# period end is the window a forecast needs covering.
_REMAINING_WINDOW_START = date(2026, 7, 7)
_REMAINING_WINDOW_END = date(2026, 8, 6)


def _seed_training_window(s: Session) -> None:
    for day, max_temp, kwh in _TRAINING_DAYS_AND_TEMPS:
        _seed_daily_consumption_summary(s, day, kwh)
        _seed_weather_observation(s, day, max_temp)


def _seed_uniform_forecast(s: Session, start: date, end: date, max_temp: float) -> None:
    day = start
    while day <= end:
        _seed_weather_forecast(s, day, max_temp)
        day += timedelta(days=1)


def _latest_gas_projected_total_cost(mariadb_client: MariaDBClient) -> Decimal:
    with mariadb_client.session_read_scope() as session:
        row = (
            session.query(model.cost_forecast)
            .filter_by(energy="G")
            .order_by(model.cost_forecast.id.desc())
            .first()
        )
    return row.projected_total_cost


@responses.activate
def test_a_colder_than_recent_average_forecast_raises_gas_projected_total_cost_above_the_flat_average_method(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")

    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
        _seed_training_window(s)
        # Every remaining day gets a forecast far colder (-6.0C, HDD 21.5)
        # than the training window's ~6.0C average, so the regression must
        # predict noticeably more than the flat 39.0 kWh/day average for
        # every one of those days.
        _seed_uniform_forecast(s, _REMAINING_WINDOW_START, _REMAINING_WINDOW_END, -6.0)

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        gas_row = session.query(model.cost_forecast).filter_by(energy="G").one()

    # actual_cost_to_date: unchanged from the existing gas fixture's single
    # elapsed day (2026-07-06) -- 48.0 kWh @ 7.00p + 29.00p standing = £3.65.
    # remaining_days = 31 (32 total period days - 1 elapsed day).
    # Regression predicts 20 + 2*21.5 = 63.0 kWh/day for every one of those
    # 31 remaining days (all get a full 24h -- as_of is exact local
    # midnight): variable_cost = 31 * 63.0 * 7.00p = 13,671.00p, standing =
    # 31 * 29.00p = 899.00p -> remaining = £145.70 -> total £149.35.
    # The flat-average method (39.0 kWh/day instead of 63.0) would instead
    # produce variable_cost = 31 * 39.0 * 7.00p = 8,463.00p -> remaining =
    # £93.62 -> total £97.27 -- strictly lower, proving the regression (not
    # a plain average) drove this result.
    assert gas_row.projected_total_cost == Decimal("149.35")


@responses.activate
def test_a_mid_day_as_of_gives_the_regression_a_fractional_first_remaining_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Every other regression test uses as_of=exact local midnight, so every
    # remaining day (including the first) gets a full 24h -- the
    # fractional-first-day branch in _hours_remaining_in_day (as_of's own
    # local day gets only the hours remaining until local midnight) is never
    # exercised. This uses a 06:00 as_of instead: 18 of day 07-07's 24 hours
    # remain (fraction 0.75).
    _mock_billing_period("2026-07-07", "2026-08-07")

    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
        _seed_training_window(s)
        _seed_uniform_forecast(s, _REMAINING_WINDOW_START, _REMAINING_WINDOW_END, -6.0)

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    as_of = start_of_local_day(date(2026, 7, 7)) + timedelta(hours=6)
    retriever.refresh(as_of=as_of)

    with mariadb_client.session_read_scope() as session:
        gas_row = session.query(model.cost_forecast).filter_by(energy="G").one()

    # A mid-day as_of makes 2026-07-07 itself a still-arriving (zero real
    # consumption seeded), gap-filled elapsed day -- standing charge only,
    # £0.29 -- alongside the existing real elapsed day 2026-07-06 (£3.65):
    # actual_cost_to_date = £3.94. remaining_days = 32 - 2 = 30, standing =
    # 30 * 29.00p = 870.00p.
    # The variable-cost loop still starts at 07-07 (as_of's own local day):
    # its 18 remaining hours (fraction 0.75) predict 63.0 kWh/day (as in the
    # exact-midnight test) -> 0.75 * 63.0 * 7.00p = 330.75p, plus 30 full
    # remaining days (07-08..08-06) at 63.0 kWh/day -> 30 * 441.00p =
    # 13,230.00p. variable_cost = 330.75 + 13,230.00 = 13,560.75p.
    # remaining = (13,560.75 + 870.00) / 100 = £144.3075 -> total £148.2475,
    # rounded to £148.25 by the cost_forecast table's 2-decimal-place
    # Numeric column on persistence.
    assert gas_row.actual_cost_to_date == Decimal("3.94")
    assert gas_row.projected_total_cost == Decimal("148.25")


@responses.activate
def test_a_milder_forecast_produces_a_lower_gas_projected_total_cost_than_a_colder_forecast(
    mariadb_client: MariaDBClient,
) -> None:
    # Same historical training window as the previous test -- proves the
    # acceptance-criteria comparison directly: given identical history, only
    # the forecast changes, and a strictly lower forecast temperature must
    # never produce a strictly lower (or equal) projected cost than a milder
    # one. If this app fell back to a flat average consumption figure
    # instead of the weather regression, both runs below would produce the
    # exact same projected_total_cost regardless of forecast temperature.
    _mock_billing_period("2026-07-07", "2026-08-07")
    _mock_billing_period("2026-07-07", "2026-08-07")

    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
        _seed_training_window(s)
        # -6.0C, HDD 21.5 -> regression predicts 20 + 2*21.5 = 63.0 kWh/day.
        _seed_uniform_forecast(s, _REMAINING_WINDOW_START, _REMAINING_WINDOW_END, -6.0)

    cold_retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    cold_retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))
    cold_projected_total_cost = _latest_gas_projected_total_cost(mariadb_client)

    with mariadb_client.session_write_scope() as s:
        # Replace the cold forecast with a mild one -- 18.0C is above the
        # 15.5C HDD base temperature, so HDD is 0 for every remaining day
        # (no heating demand at all) -- regression predicts just the
        # intercept, 20.0 kWh/day, well below both the cold run's 63.0
        # kWh/day and the flat 39.0 kWh/day average.
        s.execute(text("DELETE FROM weather_forecast"))
        _seed_uniform_forecast(s, _REMAINING_WINDOW_START, _REMAINING_WINDOW_END, 18.0)

    mild_retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    mild_retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))
    mild_projected_total_cost = _latest_gas_projected_total_cost(mariadb_client)

    assert mild_projected_total_cost < cold_projected_total_cost


@responses.activate
def test_a_remaining_day_with_no_forecast_falls_back_to_the_flat_average_while_other_remaining_days_still_use_the_forecast(
    mariadb_client: MariaDBClient,
) -> None:
    # Same training window/regression as the cold-forecast test above
    # (predicts 63.0 kWh/day), but one single remaining day in the middle of
    # the billing period has no weather_forecast row at all -- that day, and
    # only that day, must fall back to the flat average-consumption figure
    # (48.0 kWh/day, from the elapsed 2026-07-06 day's real 48.0 kWh -- see
    # test_gas_projected_total_cost_uses_the_same_average_consumption_formula_as_electricity),
    # while every other remaining day still uses the regression.
    _mock_billing_period("2026-07-07", "2026-08-07")

    day_missing_forecast = date(2026, 7, 20)

    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
        _seed_training_window(s)
        day = _REMAINING_WINDOW_START
        while day <= _REMAINING_WINDOW_END:
            if day != day_missing_forecast:
                _seed_weather_forecast(s, day, -6.0)
            day += timedelta(days=1)

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        gas_row = session.query(model.cost_forecast).filter_by(energy="G").one()

    # 30 remaining days at the regression's 63.0 kWh/day (24h each, as_of is
    # exact local midnight) + 1 remaining day (the one missing a forecast
    # row) at the flat 48.0 kWh/day average, all at 7.00p/kWh, plus standing
    # charge for all 31 remaining days.
    regression_days = 30
    variable_cost_pence = regression_days * Decimal("63.0") * Decimal("7.00") + Decimal(
        "48.0"
    ) * Decimal("7.00")
    standing_cost_pence = 31 * Decimal("29.00")
    expected_remaining = (variable_cost_pence + standing_cost_pence) / 100
    assert (
        gas_row.projected_total_cost == gas_row.actual_cost_to_date + expected_remaining
    )


@responses.activate
def test_too_few_historical_training_days_falls_back_entirely_to_the_flat_average_method(
    mariadb_client: MariaDBClient,
) -> None:
    # Only 3 valid (temp, kWh) training pairs -- below the regression's
    # minimum of 5 -- even though every single remaining day has a
    # weather_forecast row available. The guard must skip the regression
    # entirely for the whole period, not attempt (and fail) a fit per day,
    # so every remaining day falls back to the flat average -- identical to
    # test_gas_projected_total_cost_uses_the_same_average_consumption_formula_as_electricity's
    # result.
    _mock_billing_period("2026-07-07", "2026-08-07")

    sparse_training_days = _TRAINING_DAYS_AND_TEMPS[-3:]

    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
        for day, max_temp, kwh in sparse_training_days:
            _seed_daily_consumption_summary(s, day, kwh)
            _seed_weather_observation(s, day, max_temp)
        _seed_uniform_forecast(s, _REMAINING_WINDOW_START, _REMAINING_WINDOW_END, -6.0)

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        gas_row = session.query(model.cost_forecast).filter_by(energy="G").one()

    remaining_days = 31
    expected_remaining = (
        remaining_days * (Decimal("48.0") * Decimal("7.00") + Decimal("29.00")) / 100
    )
    assert (
        gas_row.projected_total_cost == gas_row.actual_cost_to_date + expected_remaining
    )


@responses.activate
def test_near_identical_training_window_temperatures_falls_back_entirely_to_the_flat_average_method(
    mariadb_client: MariaDBClient,
) -> None:
    # >=5 valid pairs, but every training day is at (near enough) the exact
    # same temperature -- degenerate HDD variance, below the regression's
    # 0.01 epsilon. A slope fit through this would be unstable/meaningless,
    # so the guard must skip the regression here too, even with a
    # genuinely colder forecast that would otherwise clearly raise the
    # projection above the flat average. Result must again match the flat
    # average method exactly.
    _mock_billing_period("2026-07-07", "2026-08-07")

    uniform_temp_training_days = [
        (date(2026, 6, 30), 10.0, "40.0"),
        (date(2026, 7, 1), 10.0, "41.0"),
        (date(2026, 7, 2), 10.0, "39.0"),
        (date(2026, 7, 3), 10.0, "40.0"),
        (date(2026, 7, 4), 10.0, "42.0"),
        (date(2026, 7, 5), 10.0, "38.0"),
        (date(2026, 7, 6), 10.0, "40.0"),
    ]

    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
        for day, max_temp, kwh in uniform_temp_training_days:
            _seed_daily_consumption_summary(s, day, kwh)
            _seed_weather_observation(s, day, max_temp)
        _seed_uniform_forecast(s, _REMAINING_WINDOW_START, _REMAINING_WINDOW_END, -6.0)

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        gas_row = session.query(model.cost_forecast).filter_by(energy="G").one()

    remaining_days = 31
    expected_remaining = (
        remaining_days * (Decimal("48.0") * Decimal("7.00") + Decimal("29.00")) / 100
    )
    assert (
        gas_row.projected_total_cost == gas_row.actual_cost_to_date + expected_remaining
    )


def test_the_fitted_regression_coefficients_reflect_higher_consumption_on_colder_days() -> (
    None
):
    # Direct unit test of the regression fit itself (AC: "its coefficients
    # reflect that relationship") -- the end-to-end tests above only prove
    # the *projection* comes out higher/lower via the regression, not that
    # the fitted line's own slope is downward (more kWh for colder days).
    # Same training data as _TRAINING_DAYS_AND_TEMPS, passed directly rather
    # than through the database.
    pairs = [
        GasTrainingDay(max_temp, Decimal(kwh))
        for _, max_temp, kwh in _TRAINING_DAYS_AND_TEMPS
    ]

    regression = fit_gas_weather_regression(pairs)

    assert regression is not None
    # A colder day (lower max_temp, higher HDD) must predict more kWh than a
    # milder day -- i.e. slope on HDD is positive. heating_degree_days is
    # monotonically decreasing in max_temp, so this is equivalent to
    # predict_daily_kwh being monotonically decreasing in max_temp.
    colder_prediction = regression.predict_daily_kwh(0.0)
    milder_prediction = regression.predict_daily_kwh(12.0)
    assert colder_prediction > milder_prediction


def test_predicted_kwh_is_floored_at_zero_for_a_negative_fitted_intercept() -> None:
    # A perfectly linear kWh = 5*HDD - 50 relationship over a genuinely cold
    # training window (HDD 10..18, so every real training kWh is still
    # non-negative) fits an intercept of -50 -- the (extrapolated) kWh at
    # HDD=0. Without a floor, predicting for a day at or above the 15.5C
    # base temperature (HDD=0) would return -50.0, a nonsensical negative
    # gas consumption and thus a negative variable cost.
    pairs = [
        GasTrainingDay(5.5, Decimal(0)),
        GasTrainingDay(3.5, Decimal(10)),
        GasTrainingDay(1.5, Decimal(20)),
        GasTrainingDay(-0.5, Decimal(30)),
        GasTrainingDay(-2.5, Decimal(40)),
    ]

    regression = fit_gas_weather_regression(pairs)

    assert regression is not None
    assert regression.intercept == -50.0
    assert regression.predict_daily_kwh(20.0) == 0.0


@responses.activate
def test_no_forecast_for_any_remaining_day_falls_back_to_the_flat_average_for_the_whole_period(
    mariadb_client: MariaDBClient,
) -> None:
    # AC: "no weather_forecast data exists for some OR ALL remaining days"
    # -- the other fallback test above only covers a single missing day
    # mixed in with regression days; this covers the "all" sub-case (e.g.
    # hive-app has never run) with no weather_forecast rows seeded at all.
    # A working regression is still fit successfully from the training
    # window (proving this is the per-day forecast-lookup fallback, not the
    # too-few-pairs guard from the test above), but every remaining day has
    # nothing to predict from and must use the flat average.
    _mock_billing_period("2026-07-07", "2026-08-07")

    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
        _seed_training_window(s)

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        gas_row = session.query(model.cost_forecast).filter_by(energy="G").one()

    remaining_days = 31
    expected_remaining = (
        remaining_days * (Decimal("48.0") * Decimal("7.00") + Decimal("29.00")) / 100
    )
    assert (
        gas_row.projected_total_cost == gas_row.actual_cost_to_date + expected_remaining
    )


@responses.activate
def test_a_non_finite_forecast_temperature_falls_back_to_the_flat_average_for_that_day(
    mariadb_client: MariaDBClient,
) -> None:
    # weather_forecast is cross-app, hive-app-owned input read via raw SQL
    # (mysql/client.py's read_weather_forecast_max_temps) -- a NaN row must
    # be treated the same as a missing forecast row for that day (falling
    # back to the flat average), not fed into the regression and silently
    # corrupting projected_total_cost with a NaN-derived value.
    _mock_billing_period("2026-07-07", "2026-08-07")

    non_finite_day = date(2026, 7, 20)

    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
        _seed_training_window(s)
        day = _REMAINING_WINDOW_START
        while day <= _REMAINING_WINDOW_END:
            temp = float("nan") if day == non_finite_day else -6.0
            _seed_weather_forecast(s, day, temp)
            day += timedelta(days=1)

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        gas_row = session.query(model.cost_forecast).filter_by(energy="G").one()

    # Same expected total as
    # test_a_remaining_day_with_no_forecast_falls_back_to_the_flat_average_while_other_remaining_days_still_use_the_forecast
    # -- a NaN forecast row for one day must produce an identical result to
    # that day having no forecast row at all.
    regression_days = 30
    variable_cost_pence = regression_days * Decimal("63.0") * Decimal("7.00") + Decimal(
        "48.0"
    ) * Decimal("7.00")
    standing_cost_pence = 31 * Decimal("29.00")
    expected_remaining = (variable_cost_pence + standing_cost_pence) / 100
    assert (
        gas_row.projected_total_cost == gas_row.actual_cost_to_date + expected_remaining
    )


def test_a_non_finite_training_observation_is_excluded_from_the_daily_max(
    mariadb_client: MariaDBClient,
) -> None:
    # weather_observation is likewise cross-app input -- a NaN/Infinity temp
    # among several observations for the same local day must be excluded
    # from that day's daily-max computation, not win the MAX comparison and
    # get selected as the training pair's temperature. Direct test of
    # MariaDBClient.read_weather_observation_daily_max_temps itself (not the
    # full retriever), since this is a read-boundary guard, not a
    # regression-fitting behaviour.
    day = date(2026, 7, 1)
    with mariadb_client.session_write_scope() as s:
        _seed_weather_observation(s, day, 9.0)
        s.execute(
            text(
                "INSERT INTO weather_observation (source, observed_at, temp) "
                "VALUES (:source, :observed_at, :temp)"
            ),
            {
                "source": "test",
                "observed_at": start_of_local_day(day) + timedelta(hours=18),
                "temp": float("nan"),
            },
        )

    daily_max = mariadb_client.read_weather_observation_daily_max_temps(day, day)

    assert daily_max == {day: 9.0}

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import responses
from sqlalchemy.orm import Session
from tests.weather_fixtures import heating_model_record
from tests.weather_fixtures import mock_billing_period as _mock_billing_period
from tests.weather_fixtures import seed_heating_forecast as _seed_heating_forecast

from common.local_day import start_of_local_day
from octopus_app.common.config import OctopusAPISettings
from octopus_app.data.cost_forecast import CostForecastRetriever
from octopus_app.data.model import (
    ConsumptionSummary,
    CostForecast,
    DailyCostSummary,
    Energy,
    HeatingForecastRecord,
    StoredHeatingModel,
)
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient, _is_missing_table_error
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

    def read_product_rates_for_local_day(
        self, product_code: str, region: str, day: date
    ) -> list[Rate]:
        return self._mariadb.read_product_rates_for_local_day(product_code, region, day)

    def read_daily_consumption_summary(
        self, energy: Energy, start_date: date, end_date: date
    ) -> list[ConsumptionSummary]:
        return self._mariadb.read_daily_consumption_summary(
            energy, start_date, end_date
        )

    def read_latest_usable_heating_model(self) -> StoredHeatingModel | None:
        return self._mariadb.read_latest_usable_heating_model()

    def read_heating_forecast_days(self) -> list[tuple[int, HeatingForecastRecord]]:
        return self._mariadb.read_heating_forecast_days()

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
# period end is a day the projection needs a figure for.
_REMAINING_WINDOW_START = date(2026, 7, 7)
_REMAINING_WINDOW_END = date(2026, 8, 6)


def _uniform_expected_gas(kwh: float) -> dict[date, float]:
    return {
        _REMAINING_WINDOW_START + timedelta(days=offset): kwh
        for offset in range((_REMAINING_WINDOW_END - _REMAINING_WINDOW_START).days + 1)
    }


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
def test_a_model_figure_above_the_flat_average_raises_gas_projected_total_cost(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")

    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
    _seed_heating_forecast(mariadb_client, _uniform_expected_gas(63.0))

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    # actual_cost_to_date: the single elapsed gas day (2026-07-06), 48.0 kWh
    # @ 7.00p + 29.00p standing = GBP 3.65. The model expects 63.0 kWh on
    # every one of the 31 remaining days (full 24h each: as_of is local
    # midnight): variable 31 * 63.0 * 7.00p = 13,671.00p, standing
    # 31 * 29.00p = 899.00p -> remaining GBP 145.70 -> total GBP 149.35. The
    # flat average (48.0 kWh/day) would give GBP 3.65 + GBP 113.15 = 116.80.
    assert _latest_gas_projected_total_cost(mariadb_client) == Decimal("149.35")


def _refresh_seeded_gas_projection(
    mariadb_client: MariaDBClient, as_of: datetime | None = None
) -> Decimal:
    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter(), _make_gas_meter()])
    )
    retriever.refresh(as_of=as_of or start_of_local_day(date(2026, 7, 7)))
    return _latest_gas_projected_total_cost(mariadb_client)


# The flat average of the one elapsed gas day: GBP 3.65 actual + 31 remaining
# days * (48.0 kWh @ 7.00p + 29.00p standing) = GBP 3.65 + GBP 113.15.
_FLAT_AVERAGE_PROJECTED_TOTAL = Decimal("116.80")


@responses.activate
def test_a_mid_day_as_of_gives_the_model_a_fractional_first_remaining_day(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")
    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
    _seed_heating_forecast(mariadb_client, _uniform_expected_gas(63.0))

    as_of = start_of_local_day(date(2026, 7, 7)) + timedelta(hours=6)
    projected = _refresh_seeded_gas_projection(mariadb_client, as_of)

    # 06:00 leaves 18 of 07-07's 24 hours (0.75). 07-07 itself is a
    # standing-charge-only gap day in the actual cost (GBP 0.29), so actual =
    # 3.65 + 0.29 = GBP 3.94. Remaining: 30 days of standing = 870.00p, and
    # variable = 0.75 * 63.0 * 7.00p (330.75p) + 30 * 63.0 * 7.00p
    # (13,230.00p) = 13,560.75p -> remaining GBP 144.3075 -> GBP 148.2475,
    # persisted to two places as GBP 148.25.
    assert projected == Decimal("148.25")


@responses.activate
def test_days_beyond_the_models_week_use_the_flat_average(
    mariadb_client: MariaDBClient,
) -> None:
    # The model job only writes today..today+6.
    week = {
        _REMAINING_WINDOW_START + timedelta(days=offset): 63.0 for offset in range(7)
    }
    _mock_billing_period("2026-07-07", "2026-08-07")
    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
    _seed_heating_forecast(mariadb_client, week)

    projected = _refresh_seeded_gas_projection(mariadb_client)

    # 7 days at 63.0 kWh + 24 days at the flat 48.0 kWh, all @ 7.00p:
    # variable 3,087.00p + 8,064.00p = 11,151.00p, standing 31 * 29.00p =
    # 899.00p -> remaining GBP 120.50 -> total GBP 124.15.
    assert projected == Decimal("124.15")


@responses.activate
def test_a_remaining_day_with_no_model_figure_uses_the_flat_average_while_the_others_use_the_model(
    mariadb_client: MariaDBClient,
) -> None:
    expected_gas = _uniform_expected_gas(63.0)
    del expected_gas[date(2026, 7, 20)]
    _mock_billing_period("2026-07-07", "2026-08-07")
    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
    _seed_heating_forecast(mariadb_client, expected_gas)

    projected = _refresh_seeded_gas_projection(mariadb_client)

    # 30 days at 63.0 kWh + 1 day at the flat 48.0 kWh, @ 7.00p, plus
    # standing for all 31: (30 * 441.00 + 336.00 + 899.00) / 100 = GBP
    # 144.65 -> total GBP 148.30.
    assert projected == Decimal("148.30")


@responses.activate
def test_figures_outside_the_remaining_days_are_ignored(
    mariadb_client: MariaDBClient,
) -> None:
    expected_gas = _uniform_expected_gas(63.0)
    expected_gas[date(2026, 7, 5)] = 500.0  # already past
    expected_gas[date(2026, 8, 20)] = 500.0  # after the billing period
    _mock_billing_period("2026-07-07", "2026-08-07")
    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
    _seed_heating_forecast(mariadb_client, expected_gas)

    assert _refresh_seeded_gas_projection(mariadb_client) == Decimal("149.35")


@responses.activate
def test_no_heating_model_falls_back_to_the_flat_average_for_the_whole_period(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")
    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)

    with caplog.at_level("INFO"):
        projected = _refresh_seeded_gas_projection(mariadb_client)

    assert projected == _FLAT_AVERAGE_PROJECTED_TOTAL
    assert "no usable heating model" in caplog.text


@responses.activate
def test_only_an_unusable_heating_model_falls_back_to_the_flat_average_for_the_whole_period(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")
    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
    _seed_heating_forecast(mariadb_client, _uniform_expected_gas(63.0), usable=False)

    assert (
        _refresh_seeded_gas_projection(mariadb_client) == _FLAT_AVERAGE_PROJECTED_TOTAL
    )


@responses.activate
def test_a_newer_unusable_refit_does_not_hide_the_latest_usable_models_forecast(
    mariadb_client: MariaDBClient,
) -> None:
    # After an unusable refit the job re-judges with the previous usable
    # model, so the forecast rows stay tied to that model, not the newest row.
    _mock_billing_period("2026-07-07", "2026-08-07")
    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
    _seed_heating_forecast(mariadb_client, _uniform_expected_gas(63.0))
    mariadb_client.write_heating_model(
        heating_model_record(datetime(2026, 7, 7, 4, 0, tzinfo=UTC), usable=False)
    )

    assert _refresh_seeded_gas_projection(mariadb_client) == Decimal("149.35")


@responses.activate
def test_forecast_rows_tied_to_a_different_model_than_the_latest_usable_one_are_ignored(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # A newer usable model has been fitted but the job has not yet rewritten
    # the forecast: the old rows belong to the older model.
    _mock_billing_period("2026-07-07", "2026-08-07")
    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
    _seed_heating_forecast(mariadb_client, _uniform_expected_gas(63.0))
    mariadb_client.write_heating_model(
        heating_model_record(datetime(2026, 7, 7, 4, 0, tzinfo=UTC))
    )

    with caplog.at_level("INFO"):
        projected = _refresh_seeded_gas_projection(mariadb_client)

    assert projected == _FLAT_AVERAGE_PROJECTED_TOTAL
    assert "do not match the latest usable heating model" in caplog.text


@responses.activate
def test_a_usable_model_with_no_forecast_rows_yet_falls_back_to_the_flat_average(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    # The model has been fitted but the job has not written its forecast yet.
    _mock_billing_period("2026-07-07", "2026-08-07")
    with mariadb_client.session_write_scope() as s:
        _seed_electricity_and_gas_fixtures(s)
    mariadb_client.write_heating_model(
        heating_model_record(datetime(2026, 7, 6, 4, 0, tzinfo=UTC))
    )

    with caplog.at_level("INFO"):
        projected = _refresh_seeded_gas_projection(mariadb_client)

    assert projected == _FLAT_AVERAGE_PROJECTED_TOTAL
    assert "do not match the latest usable heating model" not in caplog.text


class _FakeWrappedError(Exception):
    """Mimics SQLAlchemy's DBAPIError shape: the driver's own exception is
    nested under `.orig`, not the wrapper itself."""

    def __init__(self, orig: Exception) -> None:
        super().__init__(str(orig))
        self.orig = orig


def test_is_missing_table_error_recognises_the_mariadb_error_code() -> None:
    # PyMySQL/MariaDB signature: ProgrammingError wrapping an .orig whose
    # .args[0] is the numeric error code 1146 ("table doesn't exist") --
    # distinct from SQLite's message-based "no such table" signature, which
    # every other test in this file exercises indirectly via the real
    # SQLite-backed fixtures.
    orig = Exception(1146, "Table 'octopus.weather_observation' doesn't exist")

    assert _is_missing_table_error(_FakeWrappedError(orig)) is True


def test_is_missing_table_error_returns_false_for_an_unrelated_database_error() -> None:
    orig = Exception("database is locked")

    assert _is_missing_table_error(_FakeWrappedError(orig)) is False

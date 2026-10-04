from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import responses
from sqlalchemy.orm import Session

from octopus_app.common.config import OctopusAPISettings
from octopus_app.common.exceptions import APIError
from octopus_app.data.cost_forecast import CostForecastRetriever
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
    Meter,
    Rate,
)

GRAPHQL_ENDPOINT = "https://api.octopus.energy/v1/graphql/"
PRODUCT_CODE = "VAR-24-10-01"
REGION = "H"


class _RealCostForecastSource:
    """Real MariaDBClient/BillingPeriodClient underneath -- HTTP calls
    mocked via `responses`, DB is the real SQLite fixture -- with meters
    fixed up front so tests don't need to mock the account meter-information
    endpoint too. Agile forecast data is read from agile_forecast (seeded
    directly by tests), never fetched live -- that's the Agile Forecast
    Refresh job's job (data/agile_forecast.py), not this one's."""

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

    def persist_cost_forecast(self, forecast: CostForecast) -> None:
        self._mariadb.write_cost_forecast(forecast)


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
    prior_agreements: list[Agreement] | None = None,
) -> Electricity:
    return Electricity(
        mpan="1234567890123",
        serial_number="00A1234567",
        agreements=(prior_agreements or [])
        + [
            Agreement(tariff_code=tariff_code, valid_from=valid_from, valid_to=valid_to)
        ],
    )


AGILE_PRODUCT_CODE = "AGILE-24-10-01"


def _seed_agile_forecast(
    mariadb: MariaDBClient, readings: list[AgileForecastReading]
) -> None:
    mariadb.write_agile_forecast(
        REGION, readings, fetched_at=datetime(2026, 7, 6, 4, 15, tzinfo=UTC)
    )


def _flat_agile_forecast(
    start_day: date, num_days: int, unit_rate: str
) -> list[AgileForecastReading]:
    start = start_of_local_day(start_day)
    return [
        AgileForecastReading(
            period_from=start + timedelta(minutes=30 * slot),
            period_to=start + timedelta(minutes=30 * (slot + 1)),
            unit_rate=Decimal(unit_rate),
        )
        for slot in range(48 * num_days)
    ]


def _mock_billing_period(start: str, end: str, is_fixed: bool = True) -> None:
    responses.add(
        responses.POST,
        GRAPHQL_ENDPOINT,
        json={"data": {"obtainKrakenToken": {"token": "kraken-jwt-token"}}},
        status=200,
    )
    responses.add(
        responses.POST,
        GRAPHQL_ENDPOINT,
        json={
            "data": {
                "account": {
                    "billingOptions": {
                        "currentBillingPeriodStartDate": start,
                        "currentBillingPeriodEndDate": end,
                        "isFixed": is_fixed,
                    }
                }
            }
        },
        status=200,
    )


def _source(mariadb: MariaDBClient, meters: list[Meter]) -> _RealCostForecastSource:
    settings = OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
    return _RealCostForecastSource(
        mariadb,
        BillingPeriodClient(settings, KrakenTransport()),
        meters,
        REGION,
    )


@responses.activate
def test_fixed_tariff_actual_cost_and_projection(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")

    with mariadb_client.session_write_scope() as s:
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
        # One elapsed day (2026-07-06), a full 48-slot day totalling 4.8 kWh.
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )
    # Exactly local midnight starting 2026-07-07 -- that day hasn't begun
    # yet, so only 2026-07-06 counts as elapsed.
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.cost_forecast).all()

    assert len(stored) == 1
    row = stored[0]
    assert row.billing_period_start == date(2026, 7, 6)
    assert row.billing_period_end == date(2026, 8, 6)
    # (4.8 kWh @ 20.00p) + 48.00p standing charge = 144.00p -> £1.44
    assert row.actual_cost_to_date == Decimal("1.44")
    # total_period_days = Jul6..Aug6 inclusive = 32; remaining_days = 32 - 1
    # elapsed day (Jul6) = 31, at 4.8 kWh/day average, same 20.00p rate +
    # 48.00p standing charge/day.
    remaining_days = 31
    expected_remaining = (
        remaining_days * (Decimal("4.8") * Decimal("20.00") + Decimal("48.00")) / 100
    )
    assert row.projected_total_cost == row.actual_cost_to_date + expected_remaining


@responses.activate
def test_standing_charge_is_charged_exactly_once_per_day_with_a_non_midnight_as_of(
    mariadb_client: MariaDBClient,
) -> None:
    # Regression test: the job never runs at exact midnight (it ran
    # daily at 04:00 when this was written). Billing period end (Jul 10) is the last
    # inclusive billable day, so the full period is Jul6..Jul10 = 5 days.
    # With as_of = Jul8 04:00: elapsed = {Jul6, Jul7, Jul8} (3 days, each
    # already charged a full standing fee via the elapsed query/gap-fill);
    # remaining_days (whole future days, for standing charge only) =
    # 5 total - 3 elapsed = 2, representing {Jul9, Jul10} -- not {Jul8,
    # Jul9}, since remaining_days always excludes as_of.date() ("today").
    # 3 + 2 = 5 standing-charge-days total, matching the period length
    # exactly, with today's standing fee counted once, not twice.
    _mock_billing_period("2026-07-07", "2026-07-11")

    with mariadb_client.session_write_scope() as s:
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
        # Jul6, Jul7 are strictly-past elapsed days (as_of = Jul8 04:00), so
        # each needs a full 48-slot day to count -- 6.0 kWh/day, chosen
        # (rather than a rounder-looking value) so the partial-today
        # variable-cost fraction below divides out to a clean number. Jul8
        # is today's still-in-progress day: exempt from the completeness
        # guard, so a single partial reading is realistic and sufficient.
        _seed_complete_day(s, date(2026, 7, 6), "0.125")
        _seed_complete_day(s, date(2026, 7, 7), "0.125")
        s.add(
            model.consumption(
                id="E20260708000000",
                energy="E",
                period_from=datetime(2026, 7, 8, 0, 0, tzinfo=UTC),
                period_to=datetime(2026, 7, 8, 0, 30, tzinfo=UTC),
                raw_value=Decimal("6.0"),
                unit="kWh",
                est_kwh=Decimal("6.0"),
            )
        )

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )
    # Local 04:00 on 2026-07-08 (BST), not UTC 04:00 -- the latter would be
    # local 05:00, an hour later than this test's scenario intends.
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 8)) + timedelta(hours=4))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    day_charge = Decimal("6.0") * Decimal("20.00") + Decimal("48.00")  # 168.00p
    # actual_cost_to_date: 3 elapsed days (Jul6, Jul7, Jul8), each already
    # fully charged its own standing fee -- 3 * 168.00p = 504.00p -> £5.04.
    assert row.actual_cost_to_date == 3 * day_charge / 100
    # remaining_days = 2 (Jul9, Jul10) -> standing_cost = 2*48.00 = 96.00p.
    # remaining_hours spans from Jul8 04:00 through the end of Jul10
    # (period_end_boundary = Jul11 00:00) = 68 hours -- covering the rest
    # of Jul8 (already elapsed for standing-charge purposes, but NOT yet
    # priced for its remaining consumption) plus Jul9 and Jul10 in full.
    # variable_cost = (68/24) * 6.0 kWh/day * 20.00p = 340.00p.
    # remaining total = (340.00 + 96.00)/100 = £4.36.
    assert row.projected_total_cost == 3 * day_charge / 100 + Decimal("4.36")


def _seed_agile_agreement_and_rate(
    s: Session, standing_charge: str = "50.00", unit_rate: str = "25.00"
) -> None:
    s.add(
        model.agreement(
            id="E20220101000000",
            energy="E",
            product_code=AGILE_PRODUCT_CODE,
            tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}",
            valid_from=datetime(2022, 1, 1, tzinfo=UTC),
            valid_to=None,
        )
    )
    s.add(
        model.product_rate(
            id=f"{AGILE_PRODUCT_CODE}_{REGION}_202607010000",
            product_code=AGILE_PRODUCT_CODE,
            region=REGION,
            valid_from=datetime(2026, 7, 1, tzinfo=UTC),
            valid_to=None,
            unit_rate=Decimal(unit_rate),
            standing_charge=Decimal(standing_charge),
        )
    )


@responses.activate
def test_agile_tariff_costs_each_remaining_slot_at_its_own_rate_not_a_flat_average(
    mariadb_client: MariaDBClient,
) -> None:
    # A flat per-slot rate can't distinguish true per-slot costing from a
    # flat-average shortcut -- this fixture uses two different rates across
    # the only two remaining slots so an incorrect averaged implementation
    # would produce a visibly different total. UTC 22:00/22:30 on 2026-07-06
    # are the last two half-hourly slots of *local* (BST) 6 July -- local
    # 23:00-00:00.
    _mock_billing_period("2026-07-07", "2026-07-08")
    _seed_agile_forecast(
        mariadb_client,
        [
            AgileForecastReading(
                period_from=datetime(2026, 7, 6, 22, 0, tzinfo=UTC),
                period_to=datetime(2026, 7, 6, 22, 30, tzinfo=UTC),
                unit_rate=Decimal("10.00"),
            ),
            AgileForecastReading(
                period_from=datetime(2026, 7, 6, 22, 30, tzinfo=UTC),
                period_to=datetime(2026, 7, 6, 23, 0, tzinfo=UTC),
                unit_rate=Decimal("30.00"),
            ),
        ],
    )

    with mariadb_client.session_write_scope() as s:
        _seed_agile_agreement_and_rate(s, standing_charge="50.00", unit_rate="25.00")
        s.add(
            model.consumption(
                id="E20260706000000",
                energy="E",
                period_from=datetime(2026, 7, 6, 0, 0, tzinfo=UTC),
                period_to=datetime(2026, 7, 6, 0, 30, tzinfo=UTC),
                raw_value=Decimal("24.0"),
                unit="kWh",
                est_kwh=Decimal("24.0"),
            )
        )

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}"
                )
            ],
        )
    )
    # Local 23:00 on 2026-07-06 (BST) is UTC 22:00 -- still within local day
    # 6 July, at or before both mocked forecast entries.
    retriever.refresh(as_of=datetime(2026, 7, 6, 22, 0, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # actual: (24.0 kWh @ 25.00p) + 50.00p standing = 650.00p -> £6.50
    assert row.actual_cost_to_date == Decimal("6.50")
    # future_daily_kwh = 24.0 (one elapsed day); per_slot_kwh = 24.0/48 = 0.5.
    # The remaining window spans the rest of local Jul6 (the only two real
    # forecast entries) *and* all of local Jul7 (the inclusive billing
    # period end), which tiling fills by repeating those same two entries
    # (only one real source day exists to tile from) -- 4 slots total:
    # 2 * (0.5*10.00 + 0.5*30.00) = 40.00p variable; standing = 1 remaining
    # day * 50.00p = 50.00p -> (40.00+50.00)/100 = £0.90
    assert row.projected_total_cost == Decimal("6.50") + Decimal("0.90")


@responses.activate
def test_agile_tariff_prices_the_inclusive_final_billable_day_not_just_up_to_it(
    mariadb_client: MariaDBClient,
) -> None:
    # Regression test: the remaining-days slot window must extend through
    # billing_period_end's own half-hourly slots, not stop at its midnight
    # boundary. Uses two different flat rates on two different real
    # (non-tiled) remaining days so a window that silently excluded the
    # final day would produce a visibly smaller, wrong total.
    _mock_billing_period("2026-07-07", "2026-07-09")
    _seed_agile_forecast(
        mariadb_client,
        _flat_agile_forecast(date(2026, 7, 7), 1, "10.00")
        + _flat_agile_forecast(date(2026, 7, 8), 1, "50.00"),
    )

    with mariadb_client.session_write_scope() as s:
        _seed_agile_agreement_and_rate(s, standing_charge="0.00")
        # Elapsed day (Jul6) is strictly before as_of's date (Jul7), so it
        # needs a full 48-slot day -- 4.8 kWh total.
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}"
                )
            ],
        )
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # future_daily_kwh = 4.8 (one elapsed day); 0.00p standing charge
    # isolates the variable-cost total. Correct: both remaining days priced
    # -- 4.8*10.00 (Jul7) + 4.8*50.00 (Jul8, the inclusive end date) =
    # 120.00p -> £1.20. A window that excluded Jul8 would give only
    # 2.0*10.00 = 20.00p -> £0.20.
    remaining = row.projected_total_cost - row.actual_cost_to_date
    assert remaining == Decimal("2.88")


@responses.activate
def test_agile_tariff_remaining_days_within_the_real_forecast_horizon(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-07-11")
    _seed_agile_forecast(
        mariadb_client, _flat_agile_forecast(date(2026, 7, 6), 7, "15.00")
    )

    with mariadb_client.session_write_scope() as s:
        _seed_agile_agreement_and_rate(s)
        # Elapsed day (Jul6) is strictly before as_of's date (Jul7), so it
        # needs a full 48-slot day -- 4.8 kWh total.
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}"
                )
            ],
        )
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # 1 elapsed day: (4.8 kWh @ 25.00p) + 50.00p standing = 170.00p -> £1.70
    assert row.actual_cost_to_date == Decimal("1.70")
    # total_period_days = Jul6..Jul10 inclusive = 5; remaining_days = 5 - 1
    # elapsed day = 4 (Jul7-Jul10, the inclusive end date), flat 15.00p/kWh
    # throughout (all within the real forecast, no tiling):
    # 4 * (4.8*15.00 + 50.00) = 488.00p
    assert row.projected_total_cost == Decimal("1.70") + Decimal("4.88")


@responses.activate
def test_no_agile_forecast_data_raises_a_clear_error_and_writes_no_row(
    mariadb_client: MariaDBClient,
) -> None:
    # Regression test: an empty agile_forecast (fresh install where the
    # Agile Forecast Refresh job hasn't populated it yet, or a prolonged
    # outage that leaves no rows with period_from >= as_of) must fail loudly
    # rather than silently project zero variable cost -- tile_forecast_beyond
    # has nothing to tile from either, so the old "raise on empty forecast"
    # guarantee (AgilePredictClient.get_forecast raised APIError on an empty
    # response) must be preserved here now that the fetch was replaced with
    # a read.
    _mock_billing_period("2026-07-07", "2026-07-11")

    with mariadb_client.session_write_scope() as s:
        _seed_agile_agreement_and_rate(s)
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}"
                )
            ],
        )
    )

    with pytest.raises(RuntimeError, match="[Nn]o Agile forecast data found"):
        retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        assert session.query(model.cost_forecast).count() == 0


@responses.activate
def test_agile_tariff_remaining_days_beyond_the_forecast_horizon_uses_tiling(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-07-26")
    # Only 7 real days of forecast (Jul6-Jul12) -- the remaining window
    # (Jul7-Jul25, 19 days) needs Jul13-Jul25 (13 days) from tiling.
    _seed_agile_forecast(
        mariadb_client, _flat_agile_forecast(date(2026, 7, 6), 7, "15.00")
    )

    with mariadb_client.session_write_scope() as s:
        _seed_agile_agreement_and_rate(s)
        # Elapsed day (Jul6) is strictly before as_of's date (Jul7), so it
        # needs a full 48-slot day -- 4.8 kWh total.
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    retriever = CostForecastRetriever(
        _source(
            mariadb_client,
            [
                _make_electricity_meter(
                    tariff_code=f"E-1R-{AGILE_PRODUCT_CODE}-{REGION}"
                )
            ],
        )
    )
    # Should not raise despite the forecast running out before the billing
    # period ends -- tiling fills the remainder.
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Flat 15.00p rate throughout (both real and tiled days repeat the same
    # flat price), so the flat-rate formula still applies exactly:
    # total_period_days = Jul6..Jul25 inclusive = 20; remaining_days =
    # 20 - 1 elapsed day = 19 (Jul7-Jul25, the inclusive end date);
    # 19 * (4.8*15.00 + 50.00) = 2318.00p
    assert row.projected_total_cost == Decimal("1.70") + Decimal("23.18")


@responses.activate
def test_no_current_product_rate_raises_a_clear_error(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-07-11")

    with mariadb_client.session_write_scope() as s:
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
        # Covers Jul6's consumption (so the elapsed query succeeds) but
        # expires before as_of (Jul7 00:00), so the remaining-cost lookup
        # for "the current rate" finds nothing.
        s.add(
            model.product_rate(
                id=f"{PRODUCT_CODE}_{REGION}_202601010000",
                product_code=PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_to=datetime(2026, 7, 6, 12, 0, tzinfo=UTC),
                unit_rate=Decimal("20.00"),
                standing_charge=Decimal("48.00"),
            )
        )
        s.add(
            model.consumption(
                id="E20260706000000",
                energy="E",
                period_from=datetime(2026, 7, 6, 0, 0, tzinfo=UTC),
                period_to=datetime(2026, 7, 6, 0, 30, tzinfo=UTC),
                raw_value=Decimal("2.0"),
                unit="kWh",
                est_kwh=Decimal("2.0"),
            )
        )

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )

    with pytest.raises(RuntimeError, match="[Nn]o product_rate found"):
        retriever.refresh(as_of=datetime(2026, 7, 7, tzinfo=UTC))


@responses.activate
def test_kraken_unreachable_raises_and_writes_no_row(
    mariadb_client: MariaDBClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", lambda seconds: None)
    responses.add(
        responses.POST,
        GRAPHQL_ENDPOINT,
        json={"errors": [{"message": "Invalid API key"}]},
        status=200,
    )

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )

    with pytest.raises(APIError):
        retriever.refresh(as_of=datetime(2026, 7, 7, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        assert session.query(model.cost_forecast).count() == 0


@responses.activate
def test_kraken_unreachable_leaves_a_previous_row_unchanged(
    mariadb_client: MariaDBClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("octopus_app.common.decorator.time.sleep", lambda seconds: None)
    previous = CostForecast(
        billing_period_start=date(2026, 6, 6),
        billing_period_end=date(2026, 7, 6),
        actual_cost_to_date=Decimal("10.00"),
        projected_total_cost=Decimal("20.00"),
        computed_at=datetime(2026, 7, 6, tzinfo=UTC),
        energy=Energy.electricity,
    )
    mariadb_client.write_cost_forecast(previous)
    responses.add(
        responses.POST,
        GRAPHQL_ENDPOINT,
        json={"errors": [{"message": "Invalid API key"}]},
        status=200,
    )

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )
    with pytest.raises(APIError):
        retriever.refresh(as_of=datetime(2026, 7, 7, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        rows = session.query(model.cost_forecast).all()
    assert len(rows) == 1
    assert rows[0].projected_total_cost == Decimal("20.00")

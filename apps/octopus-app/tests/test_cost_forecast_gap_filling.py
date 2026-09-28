from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import responses
from sqlalchemy.orm import Session

from octopus_app.common.config import OctopusAPISettings
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
def test_a_zero_consumption_elapsed_day_gets_an_estimated_variable_cost_too(
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
        # Jul 6 has a full, complete day of consumption; Jul 7 (also
        # elapsed, as_of = Jul 8) has none at all -- no consumption row to
        # join a rate through, so it must be filled in independently. It's a
        # trailing gap (no real day settles after it before as_of), so its
        # estimated kWh is this period's own real-day average -- here just
        # Jul6's 4.8, the only real day so far.
        _seed_complete_day(s, date(2026, 7, 6), "0.1")

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 8)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Jul6: (4.8*20.00 + 48.00)/100 = 1.44; Jul7 (gap-filled at the 4.8 kWh
    # trailing average, priced at the same rate): (4.8*20.00 + 48.00)/100 =
    # 1.44.
    assert row.actual_cost_to_date == Decimal("2.88")
    # future_daily_kwh excludes the gap-filled Jul7 (not a representative
    # real "usage" day) -- average is just Jul6's 4.8, not avg([4.8, 4.8]).
    # total_period_days = Jul6..Aug6 inclusive = 32; remaining_days =
    # 32 - 2 elapsed days = 30.
    remaining_days = 30
    expected_remaining = (
        remaining_days * (Decimal("4.8") * Decimal("20.00") + Decimal("48.00")) / 100
    )
    assert row.projected_total_cost == Decimal("2.88") + expected_remaining


@responses.activate
def test_a_lag_incomplete_elapsed_day_gets_the_same_gap_fill_as_a_true_zero_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Distinct from the true-zero-consumption case above: Jul7 here has
    # real rows (10 of 48) -- Octopus's settlement lag, not an empty day.
    # It must still be excluded and gap-filled identically, and its
    # (large, if wrongly included) partial total must not leak into the
    # projection average.
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
        _seed_complete_day(s, date(2026, 7, 6), "0.1")
        # Only 10 of 48 slots have arrived for Jul7 -- each a large 5.0 kWh,
        # so an incorrect implementation that let this leak into the
        # average would produce a visibly inflated projection.
        jul7_start = start_of_local_day(date(2026, 7, 7))
        for slot in range(10):
            s.add(
                model.consumption(
                    id=f"E{(jul7_start + timedelta(minutes=30 * slot)).strftime('%Y%m%d%H%M%S')}",
                    energy="E",
                    period_from=jul7_start + timedelta(minutes=30 * slot),
                    period_to=jul7_start + timedelta(minutes=30 * (slot + 1)),
                    raw_value=Decimal("5.0"),
                    unit="kWh",
                    est_kwh=Decimal("5.0"),
                )
            )

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 8)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Jul6: (4.8*20.00 + 48.00)/100 = 1.44; Jul7 (incomplete, gap-filled at
    # the 4.8 kWh trailing average): (4.8*20.00 + 48.00)/100 = 1.44 -- Jul7's
    # real 50.0 kWh so far never counted.
    assert row.actual_cost_to_date == Decimal("2.88")
    # future_daily_kwh excludes the gap-filled Jul7 -- average is just
    # Jul6's 4.8, not a number inflated by Jul7's partial 50.0 kWh.
    remaining_days = 30
    expected_remaining = (
        remaining_days * (Decimal("4.8") * Decimal("20.00") + Decimal("48.00")) / 100
    )
    assert row.projected_total_cost == Decimal("2.88") + expected_remaining


@responses.activate
def test_an_interior_gap_day_is_interpolated_from_its_real_neighbours(
    mariadb_client: MariaDBClient,
) -> None:
    # Jul7 is missing entirely, but Jul8 (a later real day) has already
    # settled before as_of -- data has resumed, so this is an "interior" gap,
    # not a trailing one: its estimate interpolates from the real days either
    # side (Jul6's 4.8 kWh, Jul8's 14.4 kWh), not the period average alone.
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
        _seed_complete_day(s, date(2026, 7, 6), "0.1")
        _seed_complete_day(s, date(2026, 7, 8), "0.3")

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 9)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Jul6: (4.8*20.00+48.00)/100=1.44; Jul7 (interpolated at avg(4.8,14.4)
    # =9.6 kWh): (9.6*20.00+48.00)/100=2.40; Jul8: (14.4*20.00+48.00)/100
    # =3.36. Total = 1.44+2.40+3.36 = 7.20.
    assert row.actual_cost_to_date == Decimal("7.20")
    # future_daily_kwh excludes the interpolated Jul7 -- average of the two
    # real days only: avg(4.8, 14.4) = 9.6 (same figure, coincidentally,
    # since interpolation and the period average both average the same two
    # real days here). total_period_days=32; remaining_days=32-3=29.
    remaining_days = 29
    expected_remaining = (
        remaining_days * (Decimal("9.6") * Decimal("20.00") + Decimal("48.00")) / 100
    )
    assert row.projected_total_cost == Decimal("7.20") + expected_remaining


@responses.activate
def test_a_multi_day_interior_gap_applies_the_same_estimate_to_every_day_in_the_span(
    mariadb_client: MariaDBClient,
) -> None:
    # Jul7 and Jul8 are both missing between real days Jul6 and Jul9 -- a
    # two-day interior gap. Each missing day gets the SAME estimate (the
    # average of the real neighbours either side of the whole span), not a
    # linear interpolation across the span.
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
        _seed_complete_day(s, date(2026, 7, 6), "0.1")
        _seed_complete_day(s, date(2026, 7, 9), "0.5")

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 10)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # avg(4.8, 24.0) = 14.4 kWh applied to BOTH Jul7 and Jul8.
    # Jul6: (4.8*20.00+48.00)/100=1.44; Jul7: (14.4*20.00+48.00)/100=3.36;
    # Jul8: (14.4*20.00+48.00)/100=3.36; Jul9: (24.0*20.00+48.00)/100=5.28.
    # Total = 1.44+3.36+3.36+5.28 = 13.44.
    assert row.actual_cost_to_date == Decimal("13.44")
    remaining_days = 28
    expected_remaining = (
        remaining_days * (Decimal("14.4") * Decimal("20.00") + Decimal("48.00")) / 100
    )
    assert row.projected_total_cost == Decimal("13.44") + expected_remaining


@responses.activate
def test_a_trailing_gap_with_no_real_days_yet_falls_back_to_the_prior_weeks_average(
    mariadb_client: MariaDBClient,
) -> None:
    # No real consumption exists anywhere in this billing period yet (the
    # bootstrap case), but a full trailing week of already-summarized
    # consumption exists from BEFORE the period started -- that average
    # should price the gap day's variable cost, rather than falling all the
    # way back to standing-charge-only (which only happens when even that
    # history doesn't exist -- see the ultimate-fallback test below).
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
        # Trailing week before the billing period started (Jun29-Jul5), flat
        # 5.0 kWh/day -- no consumption at all for Jul6 itself.
        day = date(2026, 6, 29)
        while day <= date(2026, 7, 5):
            s.add(
                model.daily_consumption_summary(
                    energy="E", date=day, total_kwh=Decimal("5.0")
                )
            )
            day += timedelta(days=1)

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Jul6 (bootstrapped at the 5.0 kWh trailing-week average):
    # (5.0*20.00+48.00)/100 = 1.48.
    assert row.actual_cost_to_date == Decimal("1.48")
    # future_daily_kwh falls back to the (only, gap-filled) day's own 5.0
    # kWh estimate -- there's no real day yet to prefer. remaining_days =
    # 32 - 1 = 31, and as_of lands exactly on local midnight so
    # remaining_hours is an exact multiple of 24 (no fractional-day term).
    remaining_days = 31
    expected_remaining = (
        remaining_days * (Decimal("5.0") * Decimal("20.00") + Decimal("48.00")) / 100
    )
    assert row.projected_total_cost == Decimal("1.48") + expected_remaining


@responses.activate
def test_a_gap_day_under_agile_is_priced_per_half_hour_at_the_actual_published_rate(
    mariadb_client: MariaDBClient,
) -> None:
    # Jul7 is a trailing gap under an Agile agreement whose actual published
    # rate changes partway through that very day (10.00p until noon, 30.00p
    # after) -- pricing it as a single flat rate for the whole day (e.g.
    # whichever rate happens to be current at refresh time) would produce a
    # visibly different, wrong total.
    _mock_billing_period("2026-07-07", "2026-08-07")
    _seed_agile_forecast(
        mariadb_client, _flat_agile_forecast(date(2026, 7, 8), 30, "15.00")
    )

    with mariadb_client.session_write_scope() as s:
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
        # Local noon on 2026-07-07 (BST) is UTC 11:00 -- the rate changes
        # exactly at local midday, splitting Jul7 into two clean 12-hour
        # halves.
        s.add(
            model.product_rate(
                id=f"{AGILE_PRODUCT_CODE}_{REGION}_202601010000",
                product_code=AGILE_PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_to=datetime(2026, 7, 7, 11, 0, tzinfo=UTC),
                unit_rate=Decimal("10.00"),
                standing_charge=Decimal("50.00"),
            )
        )
        s.add(
            model.product_rate(
                id=f"{AGILE_PRODUCT_CODE}_{REGION}_202607071100",
                product_code=AGILE_PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 7, 7, 11, 0, tzinfo=UTC),
                valid_to=None,
                unit_rate=Decimal("30.00"),
                standing_charge=Decimal("50.00"),
            )
        )
        _seed_complete_day(s, date(2026, 7, 6), "0.1")
        # No consumption at all for Jul7.

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
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 8)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Jul6: (4.8*10.00+50.00)/100 = 0.98. Jul7 (trailing gap, estimated at
    # the 4.8 kWh period average, split half-and-half across the rate
    # change): (0.5*4.8*10.00 + 0.5*4.8*30.00 + 50.00)/100
    # = (24.00 + 72.00 + 50.00)/100 = 1.46.
    assert row.actual_cost_to_date == Decimal("0.98") + Decimal("1.46")
    # Remaining period (Jul8-Aug6, 30 days) prices each half-hour at the
    # flat 15.00p Agile forecast; standing charge uses the rate current as
    # of as_of (the second rate, 50.00p).
    per_slot_kwh = Decimal("4.8") / 48
    variable_remaining = 30 * 48 * per_slot_kwh * Decimal("15.00")
    standing_remaining = 30 * Decimal("50.00")
    expected_remaining = (variable_remaining + standing_remaining) / 100
    assert (
        row.projected_total_cost
        == Decimal("0.98") + Decimal("1.46") + expected_remaining
    )


@responses.activate
def test_a_gap_day_spanning_a_standing_charge_change_uses_the_midday_rate(
    mariadb_client: MariaDBClient,
) -> None:
    # Jul7 is a trailing gap whose two overlapping rates have DIFFERENT
    # standing charges (60.00p before local noon, 40.00p from noon on) but
    # the SAME unit rate -- isolating standing-charge selection from the
    # per-half-hour variable-cost weighting already covered by the Agile
    # test above. Picking max(60.00, 40.00) = 60.00 across the day's rates
    # would be wrong; the correct £1.36 below only comes from using
    # whichever rate covers local midday (40.00), the flat per-day fee's
    # existing convention.
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
        # Local noon on 2026-07-07 (BST) is UTC 11:00.
        s.add(
            model.product_rate(
                id=f"{PRODUCT_CODE}_{REGION}_202601010000",
                product_code=PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_to=datetime(2026, 7, 7, 11, 0, tzinfo=UTC),
                unit_rate=Decimal("20.00"),
                standing_charge=Decimal("60.00"),
            )
        )
        s.add(
            model.product_rate(
                id=f"{PRODUCT_CODE}_{REGION}_202607071100",
                product_code=PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 7, 7, 11, 0, tzinfo=UTC),
                valid_to=None,
                unit_rate=Decimal("20.00"),
                standing_charge=Decimal("40.00"),
            )
        )
        _seed_complete_day(s, date(2026, 7, 6), "0.1")
        # No consumption at all for Jul7.

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 8)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Jul6 (fully covered by the first rate): (4.8*20.00+60.00)/100 = 1.56.
    # Jul7 (trailing gap at the 4.8 kWh period average, same unit rate all
    # day, standing charge from the midday-covering second rate):
    # (4.8*20.00+40.00)/100 = 1.36.
    assert row.actual_cost_to_date == Decimal("1.56") + Decimal("1.36")
    # Remaining period: as_of (Jul8 00:00) falls within the second rate, so
    # its 40.00p standing charge applies to every remaining day too.
    remaining_days = 30
    expected_remaining = (
        remaining_days * (Decimal("4.8") * Decimal("20.00") + Decimal("40.00")) / 100
    )
    assert (
        row.projected_total_cost
        == Decimal("1.56") + Decimal("1.36") + expected_remaining
    )


@responses.activate
def test_a_gap_day_with_genuinely_overlapping_rates_resolves_to_one_winner_per_instant(
    mariadb_client: MariaDBClient,
) -> None:
    # Unlike the clean-handover case above, these two rates genuinely
    # overlap for two hours either side of local midday (Jul7 10:00-12:00
    # UTC) rather than meeting at a boundary, and use DIFFERENT unit rates
    # so double-billing the overlap is visible in the total, not just the
    # standing charge. Picking "whichever comes first" (ascending
    # valid_from) for the standing charge would silently pick the OLDER
    # rate; summing every rate's own full clipped window for the variable
    # cost (rather than partitioning the day into non-overlapping,
    # single-winner segments first) would double-bill the 2h overlap. Both
    # must resolve to the newer rate winning that overlap, matching
    # read_current_product_rate's "most-recently-started wins" tiebreak.
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
        # Older rate: valid from 2022, ends Jul7 12:00 UTC (day_start+13h).
        s.add(
            model.product_rate(
                id=f"{PRODUCT_CODE}_{REGION}_202601010000",
                product_code=PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2022, 1, 1, tzinfo=UTC),
                valid_to=datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
                unit_rate=Decimal("10.00"),
                standing_charge=Decimal("70.00"),
            )
        )
        # Newer rate: starts Jul7 10:00 UTC (day_start+11h, entirely within
        # Jul7 -- Jul6 is untouched by it), open-ended.
        s.add(
            model.product_rate(
                id=f"{PRODUCT_CODE}_{REGION}_202607071000",
                product_code=PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 7, 7, 10, 0, tzinfo=UTC),
                valid_to=None,
                unit_rate=Decimal("30.00"),
                standing_charge=Decimal("30.00"),
            )
        )
        _seed_complete_day(s, date(2026, 7, 6), "0.1")
        # No consumption at all for Jul7.

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 8)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Jul6 (only the older rate ever touches it): (4.8*10.00+70.00)/100
    # = 1.18.
    # Jul7 (trailing gap at the 4.8 kWh period average): the day partitions
    # into three non-overlapping segments -- 11h at the older rate
    # (Jul6 23:00-Jul7 10:00), 2h at the newer rate once it starts and wins
    # the overlap (Jul7 10:00-12:00), and 11h at the newer rate after the
    # older one expires (Jul7 12:00-23:00): variable_cost =
    # (11*4.8*10.00 + 2*4.8*30.00 + 11*4.8*30.00)/24 = (528+288+1584)/24
    # = 100.00p exactly -- not 104.00p, which is what summing each rate's
    # own full 13h clipped window independently (double-billing the 2h
    # overlap) would produce. Standing charge uses the newer rate (30.00,
    # winning the segment that contains local midday):
    # (100.00+30.00)/100 = 1.30.
    assert row.actual_cost_to_date == Decimal("1.18") + Decimal("1.30")
    # Remaining period: as_of (Jul8 00:00) is only covered by the newer
    # rate (the older one ended Jul7 12:00 UTC), so its 30.00p standing
    # charge and 30.00p unit rate apply to every remaining day.
    remaining_days = 30
    expected_remaining = (
        remaining_days * (Decimal("4.8") * Decimal("30.00") + Decimal("30.00")) / 100
    )
    assert (
        row.projected_total_cost
        == Decimal("1.18") + Decimal("1.30") + expected_remaining
    )


@responses.activate
def test_an_interior_gap_before_any_real_day_interpolates_from_the_later_day_alone(
    mariadb_client: MariaDBClient,
) -> None:
    # Jul6 (the billing period's first day) has no consumption at all -- no
    # earlier real day exists to average with. Jul7 is a later real day, so
    # this is still an interior gap (data has resumed by as_of); its
    # estimate interpolates from the later day alone, not the period average
    # a trailing gap would use.
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
        # No consumption at all for Jul6.
        _seed_complete_day(s, date(2026, 7, 7), "0.2")

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 8)))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Jul6 (interpolated from Jul7's 9.6 kWh alone, no earlier day to
    # average with): (9.6*20.00+48.00)/100 = 2.40. Jul7 (real):
    # (9.6*20.00+48.00)/100 = 2.40 -- coincidentally the same kWh, since it
    # was the sole source of Jul6's estimate.
    assert row.actual_cost_to_date == Decimal("2.40") + Decimal("2.40")
    # future_daily_kwh excludes the interpolated Jul6 -- average is just
    # Jul7's real 9.6.
    remaining_days = 30
    expected_remaining = (
        remaining_days * (Decimal("9.6") * Decimal("20.00") + Decimal("48.00")) / 100
    )
    assert (
        row.projected_total_cost
        == Decimal("2.40") + Decimal("2.40") + expected_remaining
    )


@responses.activate
def test_the_only_elapsed_day_being_gap_filled_still_produces_a_forecast(
    mariadb_client: MariaDBClient,
) -> None:
    # Day 1 of a billing period, run early (04:00) before that day's own
    # consumption has arrived at all -- the only elapsed day is gap-filled
    # (zero real kWh), so filtering gap-filled days out of the projection
    # average would otherwise leave nothing to average, raising instead of
    # producing a (admittedly rough) forecast.
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
        # No consumption at all for Jul 6.

    retriever = CostForecastRetriever(
        _source(mariadb_client, [_make_electricity_meter()])
    )
    retriever.refresh(as_of=datetime(2026, 7, 6, 4, 0, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Jul6 (zero kWh, gap-filled): 48.00/100 = 0.48.
    assert row.actual_cost_to_date == Decimal("0.48")
    # No real day to average -- falls back to the gap-filled day itself
    # (0 kWh) rather than raising, matching pre-guard behavior for this
    # bootstrapping case.
    remaining_days = 31
    expected_remaining = (
        remaining_days * (Decimal(0) * Decimal("20.00") + Decimal("48.00")) / 100
    )
    assert row.projected_total_cost == Decimal("0.48") + expected_remaining


@responses.activate
def test_no_product_rate_for_a_zero_consumption_elapsed_day_raises_and_writes_no_row(
    mariadb_client: MariaDBClient,
) -> None:
    # A missing rate for a gap-filled (zero-consumption) elapsed day must
    # fail the whole refresh, not silently omit that day's standing charge
    # from actual_cost_to_date -- money calculations shouldn't quietly
    # produce a plausible-but-wrong number, matching this file's established
    # "raise rather than guess" philosophy elsewhere (e.g. the current-rate
    # lookup in _project_remaining_cost).
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
        # Rate A covers only the first half of Jul6 (until 12:00); rate B
        # only starts on Jul7 13:00 but does cover as_of (Jul8 00:00), so the
        # *remaining-cost* lookup succeeds -- but the gap between them
        # (Jul6 12:00-Jul7 13:00) leaves both Jul6 and Jul7 (neither of
        # which has a complete day of real consumption) without full-day
        # rate coverage, isolating the gap-fill path's own rate lookup from
        # the already-tested remaining-cost lookup.
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
            model.product_rate(
                id=f"{PRODUCT_CODE}_{REGION}_202607071300",
                product_code=PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 7, 7, 13, 0, tzinfo=UTC),
                valid_to=None,
                unit_rate=Decimal("22.00"),
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
        retriever.refresh(as_of=datetime(2026, 7, 8, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        assert session.query(model.cost_forecast).count() == 0

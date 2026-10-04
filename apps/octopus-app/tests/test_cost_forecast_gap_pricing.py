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
def test_todays_standing_charge_only_day_is_priced_when_the_last_agile_hour_is_unpublished(
    mariadb_client: MariaDBClient,
) -> None:
    # The live failure. The daily run is at 04:00 UTC (05:00 BST). Today has no
    # consumption yet, so it is a gap day priced standing-charge-only. Agile
    # publishes each afternoon to 23:00 UK time the next day -- in BST that is
    # 22:00 UTC, one hour short of the local day's end (23:00 UTC) -- so the
    # published rates stop at 22:00 UTC on 2026-10-03. The standing charge only
    # reads the rate at local midday, so the unpublished final hour must not
    # block the whole forecast.
    _mock_billing_period("2026-10-03", "2026-11-03")
    _seed_agile_forecast(
        mariadb_client, _flat_agile_forecast(date(2026, 10, 4), 30, "15.00")
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
        s.add(
            model.product_rate(
                id=f"{AGILE_PRODUCT_CODE}_{REGION}_202601010000",
                product_code=AGILE_PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_to=datetime(2026, 10, 3, 22, 0, tzinfo=UTC),
                unit_rate=Decimal("10.00"),
                standing_charge=Decimal("50.00"),
            )
        )
        _seed_complete_day(s, date(2026, 10, 2), "0.1")
        # No consumption at all for 2026-10-03 (today).

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
    retriever.refresh(as_of=datetime(2026, 10, 3, 4, 0, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Oct2 (complete): (4.8*10.00+50.00)/100 = 0.98. Today (gap, standing
    # charge only, read at local midday): 50.00/100 = 0.50.
    assert row.actual_cost_to_date == Decimal("0.98") + Decimal("0.50")


@pytest.mark.parametrize(
    "rate_ends_at",
    [
        datetime(2026, 10, 3, 10, 0, tzinfo=UTC),
        # The window is half-open: a rate ending exactly at local midday does
        # not cover it, and with no successor rate nothing does.
        datetime(2026, 10, 3, 11, 0, tzinfo=UTC),
    ],
    ids=["an-hour-before-midday", "exactly-at-midday"],
)
@responses.activate
def test_todays_standing_charge_only_day_still_fails_when_no_rate_covers_local_midday(
    mariadb_client: MariaDBClient,
    rate_ends_at: datetime,
) -> None:
    # Relaxing the coverage check must not mean "never check": the standing
    # charge is read at local midday (2026-10-03 11:00 UTC in BST), so a
    # published rate that stops before then leaves nothing to price it with
    # and the forecast must fail loudly rather than persist a wrong total.
    _mock_billing_period("2026-10-03", "2026-11-03")

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
        s.add(
            model.product_rate(
                id=f"{AGILE_PRODUCT_CODE}_{REGION}_202601010000",
                product_code=AGILE_PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_to=rate_ends_at,
                unit_rate=Decimal("10.00"),
                standing_charge=Decimal("50.00"),
            )
        )
        _seed_complete_day(s, date(2026, 10, 2), "0.1")
        # No consumption at all for 2026-10-03 (today).

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

    with pytest.raises(
        RuntimeError, match=rf"{AGILE_PRODUCT_CODE}.*2026-10-03.*midday"
    ):
        retriever.refresh(as_of=datetime(2026, 10, 3, 4, 0, tzinfo=UTC))


@responses.activate
def test_a_fully_covered_standing_charge_only_day_uses_the_rate_at_local_midday(
    mariadb_client: MariaDBClient,
) -> None:
    # Today (no consumption yet) is fully covered by two rates with different
    # standing charges (60.00p before local noon, 40.00p from noon on); the
    # day's single charge comes from the rate covering local midday.
    _mock_billing_period("2026-10-03", "2026-11-03")
    _seed_agile_forecast(
        mariadb_client, _flat_agile_forecast(date(2026, 10, 4), 30, "15.00")
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
        # Local noon on 2026-10-03 (BST) is UTC 11:00.
        s.add(
            model.product_rate(
                id=f"{AGILE_PRODUCT_CODE}_{REGION}_202601010000",
                product_code=AGILE_PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_to=datetime(2026, 10, 3, 11, 0, tzinfo=UTC),
                unit_rate=Decimal("10.00"),
                standing_charge=Decimal("60.00"),
            )
        )
        s.add(
            model.product_rate(
                id=f"{AGILE_PRODUCT_CODE}_{REGION}_202610031100",
                product_code=AGILE_PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 10, 3, 11, 0, tzinfo=UTC),
                valid_to=None,
                unit_rate=Decimal("10.00"),
                standing_charge=Decimal("40.00"),
            )
        )
        _seed_complete_day(s, date(2026, 10, 2), "0.1")

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
    retriever.refresh(as_of=datetime(2026, 10, 3, 4, 0, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    # Oct2: (4.8*10.00+60.00)/100 = 1.08. Today: 40.00/100 = 0.40.
    assert row.actual_cost_to_date == Decimal("1.08") + Decimal("0.40")


@responses.activate
def test_a_past_gap_day_needing_a_variable_cost_with_incomplete_rates_is_estimated_and_flagged(
    mariadb_client: MariaDBClient,
) -> None:
    # Oct3 is a strictly-past trailing gap (as_of is Oct4) needing a
    # variable cost, and the published rates end at 22:00 UTC, an hour short
    # of the day's local end (23:00 UTC). The unpublished final hour is priced
    # at the day's average published rate (a flat 10p here) and the day is
    # flagged, rather than the whole refresh failing.
    _mock_billing_period("2026-10-03", "2026-11-03")
    _seed_agile_forecast(
        mariadb_client, _flat_agile_forecast(date(2026, 10, 4), 31, "15.00")
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
        s.add(
            model.product_rate(
                id=f"{AGILE_PRODUCT_CODE}_{REGION}_202601010000",
                product_code=AGILE_PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 1, 1, tzinfo=UTC),
                valid_to=datetime(2026, 10, 3, 22, 0, tzinfo=UTC),
                unit_rate=Decimal("10.00"),
                standing_charge=Decimal("50.00"),
            )
        )
        # Rates resume from 23:00 UTC so today's standing charge is readable.
        s.add(
            model.product_rate(
                id=f"{AGILE_PRODUCT_CODE}_{REGION}_202610032300",
                product_code=AGILE_PRODUCT_CODE,
                region=REGION,
                valid_from=datetime(2026, 10, 3, 23, 0, tzinfo=UTC),
                valid_to=None,
                unit_rate=Decimal("10.00"),
                standing_charge=Decimal("50.00"),
            )
        )
        _seed_complete_day(s, date(2026, 10, 2), "0.1")
        # No consumption at all for 2026-10-03 (a past trailing gap by Oct4).

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

    retriever.refresh(as_of=datetime(2026, 10, 4, 4, 0, tzinfo=UTC))

    with mariadb_client.session_read_scope() as session:
        row = session.query(model.cost_forecast).one()

    assert row.rates_estimated is True
    assert row.estimated_days == "2026-10-03"
    # Oct2: 0.98; Oct3: (4.8 kWh * 10p + 50p)/100 = 0.98; today: 0.50.
    assert row.actual_cost_to_date == Decimal("0.98") + Decimal("0.98") + Decimal(
        "0.50"
    )

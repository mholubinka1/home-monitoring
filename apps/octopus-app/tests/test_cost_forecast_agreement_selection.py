from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import responses
from sqlalchemy.orm import Session

from octopus_app.common.config import OctopusAPISettings
from octopus_app.data.cost_forecast import CostForecastRetriever
from octopus_app.data.local_day import start_of_local_day
from octopus_app.data.model import CostForecast, DailyCostSummary, Energy
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
    fixed up front so tests don't need to mock the account
    meter-information endpoint too."""

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
def test_no_electricity_meter_raises_a_clear_error(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")
    retriever = CostForecastRetriever(_source(mariadb_client, []))

    with pytest.raises(RuntimeError, match="[Nn]o electricity meter"):
        retriever.refresh(as_of=datetime(2026, 7, 7, tzinfo=UTC))


@responses.activate
def test_no_current_agreement_raises_a_clear_error(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_billing_period("2026-07-07", "2026-08-07")
    lapsed_meter = Electricity(
        mpan="1234567890123",
        serial_number="00A1234567",
        agreements=[
            Agreement(
                tariff_code=f"E-1R-{PRODUCT_CODE}-{REGION}",
                valid_from=datetime(2020, 1, 1, tzinfo=UTC),
                valid_to=datetime(2021, 1, 1, tzinfo=UTC),
            )
        ],
    )
    retriever = CostForecastRetriever(_source(mariadb_client, [lapsed_meter]))

    with pytest.raises(RuntimeError, match="[Nn]o current .*agreement"):
        retriever.refresh(as_of=datetime(2026, 7, 7, tzinfo=UTC))


def _seed_fixed_tariff_agreement_and_rate(s: Session) -> None:
    # Feeds read_elapsed_billing_period_costs's DB-level consumption-to-
    # agreement join only -- unrelated to _current_electricity_agreement's
    # in-memory selection, which reads solely from the Electricity meter's
    # Agreement list passed to CostForecastRetriever.
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
    # Elapsed day (Jul6), strictly before as_of's date (Jul7) in both
    # callers of this helper -- needs a full 48-slot day, 4.8 kWh total.
    _seed_complete_day(s, date(2026, 7, 6), "0.1")


@responses.activate
def test_current_agreement_with_a_bounded_valid_to_still_matches(
    mariadb_client: MariaDBClient,
) -> None:
    # Regression test: real Agile contracts renew as fixed one-year terms, so
    # Octopus's API never returns valid_to=None even for the currently-active
    # agreement. Mirrors the real account's shape (a lapsed prior agreement
    # plus a bounded current one) so the current one is proven to be selected
    # by range, not merely "the only agreement present".
    _mock_billing_period("2026-07-07", "2026-08-07")

    with mariadb_client.session_write_scope() as s:
        _seed_fixed_tariff_agreement_and_rate(s)

    electricity_meter = _make_electricity_meter(
        valid_from=datetime(2026, 5, 24, tzinfo=UTC),
        valid_to=datetime(2027, 5, 24, tzinfo=UTC),
        prior_agreements=[
            Agreement(
                tariff_code=f"E-1R-{PRODUCT_CODE}-{REGION}",
                valid_from=datetime(2025, 5, 24, tzinfo=UTC),
                valid_to=datetime(2026, 5, 24, tzinfo=UTC),
            )
        ],
    )
    retriever = CostForecastRetriever(_source(mariadb_client, [electricity_meter]))
    retriever.refresh(as_of=start_of_local_day(date(2026, 7, 7)))

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.cost_forecast).all()

    assert len(stored) == 1
    assert stored[0].actual_cost_to_date == Decimal("1.44")


@responses.activate
@pytest.mark.parametrize(
    "valid_from, valid_to, should_match",
    [
        pytest.param(
            datetime(2026, 7, 7, tzinfo=UTC),
            datetime(2027, 7, 7, tzinfo=UTC),
            True,
            id="valid_from_boundary_is_inclusive",
        ),
        pytest.param(
            datetime(2022, 1, 1, tzinfo=UTC),
            datetime(2026, 7, 7, tzinfo=UTC),
            False,
            id="valid_to_boundary_is_exclusive",
        ),
    ],
)
def test_current_agreement_half_open_interval_boundaries(
    mariadb_client: MariaDBClient,
    valid_from: datetime,
    valid_to: datetime,
    should_match: bool,
) -> None:
    # valid_from is inclusive, valid_to is exclusive -- otherwise a renewal's
    # first instant would match both the expiring and incoming agreement.
    _mock_billing_period("2026-07-07", "2026-08-07")
    as_of = datetime(2026, 7, 7, tzinfo=UTC)

    with mariadb_client.session_write_scope() as s:
        _seed_fixed_tariff_agreement_and_rate(s)

    electricity_meter = _make_electricity_meter(
        valid_from=valid_from, valid_to=valid_to
    )
    retriever = CostForecastRetriever(_source(mariadb_client, [electricity_meter]))

    if should_match:
        retriever.refresh(as_of=as_of)
        with mariadb_client.session_read_scope() as session:
            assert session.query(model.cost_forecast).count() == 1
    else:
        with pytest.raises(RuntimeError, match="[Nn]o current .*agreement"):
            retriever.refresh(as_of=as_of)

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import responses
from tests.test_pricing_retrieval import PRODUCTS_ENDPOINT, _make_source

from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.octopus.model import Agreement, Electricity, Rate
from octopus_app.data.pricing import PricingRetriever

REGION = "C"
RETENTION_DAYS = 45
GO_PRODUCT_CODE = "GO-VAR-22-10-14"
GO_TARIFF_CODE = f"E-1R-{GO_PRODUCT_CODE}-{REGION}"
PEAK_RATE = 34.5297
OFF_PEAK_RATE = 9.4
REFRESHED_AT = datetime(2026, 11, 5, 9, 34, tzinfo=UTC)
GO_STARTED_AT = datetime(2026, 11, 5, tzinfo=UTC)


def _go_meter() -> Electricity:
    return Electricity(
        mpan="1234567890123",
        serial_number="00A1234567",
        agreements=[
            Agreement(
                tariff_code=GO_TARIFF_CODE, valid_from=GO_STARTED_AT, valid_to=None
            )
        ],
    )


def _mock_empty_catalogue() -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )


def _mock_go_rates_published_to_tonight(today: date = date(2026, 11, 5)) -> None:
    # Octopus publishes Go only to the end of the current day, whatever
    # period_to is asked for: the live response on 2026-11-05. Written for a
    # GMT day; the UTC instants are the same wall-clock times as on `today`.
    midnight = datetime(today.year, today.month, today.day, tzinfo=UTC)

    def at(days: int, hour: int, minute: int) -> str:
        instant = midnight + timedelta(days=days, hours=hour, minutes=minute)
        return instant.strftime("%Y-%m-%dT%H:%M:%SZ")

    responses.add(
        responses.GET,
        f"{PRODUCTS_ENDPOINT}{GO_PRODUCT_CODE}/electricity-tariffs/"
        f"{GO_TARIFF_CODE}/standard-unit-rates/",
        json={
            "results": [
                {
                    "value_inc_vat": PEAK_RATE,
                    "valid_from": at(0, 5, 30),
                    "valid_to": at(1, 0, 30),
                },
                {
                    "value_inc_vat": OFF_PEAK_RATE,
                    "valid_from": at(0, 0, 30),
                    "valid_to": at(0, 5, 30),
                },
                {
                    "value_inc_vat": PEAK_RATE,
                    "valid_from": at(-1, 5, 30),
                    "valid_to": at(0, 0, 30),
                },
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"{PRODUCTS_ENDPOINT}{GO_PRODUCT_CODE}/electricity-tariffs/"
        f"{GO_TARIFF_CODE}/standing-charges/",
        json={
            "results": [
                {
                    "value_inc_vat": 42.2934,
                    "valid_from": "2026-04-01T00:00:00Z",
                    "valid_to": None,
                }
            ],
            "next": None,
        },
        status=200,
    )


class _ProjectingPricingSource:
    """The real pricing source plus the reads and writes a Tariff Projection
    needs, all backed by the same MariaDBClient."""

    def __init__(self, mariadb_client: MariaDBClient, meter: Electricity) -> None:
        self._source = _make_source(mariadb_client, [meter], region_code=REGION)
        self._mariadb = mariadb_client

    def __getattr__(self, name: str) -> object:
        return getattr(self._source, name)

    def read_product_rates_for_local_day(
        self, product_code: str, region: str, day: date
    ) -> list[Rate]:
        return self._mariadb.read_product_rates_for_local_day(product_code, region, day)

    def persist_tariff_projection(
        self,
        product_code: str,
        region: str,
        slots: list[tuple[datetime, Decimal]],
        projected_at: datetime,
    ) -> None:
        self._mariadb.write_tariff_projection(product_code, region, slots, projected_at)


def _projected_rates(mariadb_client: MariaDBClient) -> dict[datetime, Decimal]:
    tp = model.tariff_projection
    with mariadb_client.session_read_scope() as session:
        return {
            row.period_from.replace(tzinfo=UTC): row.unit_rate
            for row in session.query(tp).filter(
                tp.product_code == GO_PRODUCT_CODE, tp.region == REGION
            )
        }


@responses.activate
def test_tonights_unpublished_go_cheap_window_is_projected_at_the_off_peak_rate(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_empty_catalogue()
    _mock_go_rates_published_to_tonight()
    source = _ProjectingPricingSource(mariadb_client, _go_meter())

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    projected = _projected_rates(mariadb_client)
    first_projected = datetime(2026, 11, 6, 0, 30, tzinfo=UTC)
    assert min(projected) == first_projected
    cheap_window = [first_projected + timedelta(minutes=30 * i) for i in range(10)]
    assert all(projected[slot] == Decimal("9.4") for slot in cheap_window)
    assert projected[datetime(2026, 11, 6, 5, 30, tzinfo=UTC)] == Decimal("34.5297")
    assert projected[datetime(2026, 11, 7, 0, 30, tzinfo=UTC)] == Decimal("9.4")
    assert max(projected) == REFRESHED_AT.replace(minute=30) + timedelta(days=6)


@responses.activate
def test_published_half_hours_are_not_projected(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_empty_catalogue()
    _mock_go_rates_published_to_tonight()
    source = _ProjectingPricingSource(mariadb_client, _go_meter())

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    published_until = datetime(2026, 11, 6, 0, 30, tzinfo=UTC)
    assert all(slot >= published_until for slot in _projected_rates(mariadb_client))


@responses.activate
def test_projected_cheap_half_hours_stay_at_local_off_peak_across_a_clock_change(
    mariadb_client: MariaDBClient,
) -> None:
    as_of = datetime(2027, 3, 26, 9, 34, tzinfo=UTC)
    _mock_empty_catalogue()
    _mock_go_rates_published_to_tonight(today=date(2027, 3, 26))
    source = _ProjectingPricingSource(mariadb_client, _go_meter())

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=as_of)

    projected = _projected_rates(mariadb_client)
    # 00:30-05:30 BST on 2027-03-29, after the clocks went forward on the 28th.
    cheap_start = datetime(2027, 3, 28, 23, 30, tzinfo=UTC)
    cheap_window = [cheap_start + timedelta(minutes=30 * i) for i in range(10)]
    assert all(projected[slot] == Decimal("9.4") for slot in cheap_window)
    assert projected[cheap_start - timedelta(minutes=30)] == Decimal("34.5297")
    assert projected[cheap_start + timedelta(hours=5)] == Decimal("34.5297")


@responses.activate
def test_nothing_is_projected_for_an_agile_agreement(
    mariadb_client: MariaDBClient,
) -> None:
    agile_tariff_code = "E-1R-AGILE-24-10-01-C"
    agile_meter = Electricity(
        mpan="1234567890123",
        serial_number="00A1234567",
        agreements=[
            Agreement(
                tariff_code=agile_tariff_code,
                valid_from=GO_STARTED_AT,
                valid_to=None,
            )
        ],
    )
    _mock_empty_catalogue()
    for endpoint in ("standard-unit-rates", "standing-charges"):
        responses.add(
            responses.GET,
            f"{PRODUCTS_ENDPOINT}AGILE-24-10-01/electricity-tariffs/"
            f"{agile_tariff_code}/{endpoint}/",
            json={"results": [], "next": None},
            status=200,
        )
    source = _ProjectingPricingSource(mariadb_client, agile_meter)

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    with mariadb_client.session_read_scope() as session:
        assert session.query(model.tariff_projection).count() == 0


@responses.activate
def test_a_product_without_a_fully_published_day_projects_nothing_but_still_syncs_rates(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    _mock_empty_catalogue()
    responses.add(
        responses.GET,
        f"{PRODUCTS_ENDPOINT}{GO_PRODUCT_CODE}/electricity-tariffs/"
        f"{GO_TARIFF_CODE}/standard-unit-rates/",
        json={
            "results": [
                {
                    "value_inc_vat": OFF_PEAK_RATE,
                    "valid_from": "2026-11-05T00:30:00Z",
                    "valid_to": "2026-11-05T05:30:00Z",
                }
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"{PRODUCTS_ENDPOINT}{GO_PRODUCT_CODE}/electricity-tariffs/"
        f"{GO_TARIFF_CODE}/standing-charges/",
        json={
            "results": [
                {
                    "value_inc_vat": 42.2934,
                    "valid_from": "2026-04-01T00:00:00Z",
                    "valid_to": None,
                }
            ],
            "next": None,
        },
        status=200,
    )
    source = _ProjectingPricingSource(mariadb_client, _go_meter())

    with caplog.at_level(logging.WARNING):
        PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    assert _projected_rates(mariadb_client) == {}
    assert GO_PRODUCT_CODE in caplog.text
    with mariadb_client.session_read_scope() as session:
        assert session.query(model.product_rate).count() == 1


@responses.activate
def test_a_new_projection_replaces_the_one_from_an_earlier_refresh(
    mariadb_client: MariaDBClient,
) -> None:
    earlier_slot = datetime(2026, 11, 4, 0, 30, tzinfo=UTC)
    mariadb_client.write_tariff_projection(
        GO_PRODUCT_CODE, REGION, [(earlier_slot, Decimal(1))], REFRESHED_AT
    )
    _mock_empty_catalogue()
    _mock_go_rates_published_to_tonight()
    source = _ProjectingPricingSource(mariadb_client, _go_meter())

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    projected = _projected_rates(mariadb_client)
    assert earlier_slot not in projected
    assert datetime(2026, 11, 6, 0, 30, tzinfo=UTC) in projected

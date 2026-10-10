import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import responses

from octopus_app.common.config import OctopusAPISettings
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.octopus.api import OctopusEnergyAPIClient
from octopus_app.data.octopus.model import (
    Agreement,
    Electricity,
    Gas,
    Meter,
    Product,
    Rate,
)
from octopus_app.data.pricing import PricingRetriever

PRODUCTS_ENDPOINT = "https://api.octopus.energy/v1/products/"

RETENTION_DAYS = 45
REFRESHED_AT = datetime(2026, 1, 15, 12, tzinfo=UTC)
WINDOW_START_Z = "2025-12-01T12:00:00Z"  # REFRESHED_AT minus RETENTION_DAYS


class _RealPricingSource:
    """A real PricingSource adapter for tests: genuine OctopusEnergyAPIClient
    and MariaDBClient underneath, with meters/region_code fixed up front
    rather than fetched, so tests only need to mock the HTTP endpoints
    PricingRetriever.refresh() actually calls."""

    def __init__(
        self,
        octopus: OctopusEnergyAPIClient,
        mariadb: MariaDBClient,
        meters: list[Meter],
        region_code: str,
    ) -> None:
        self._octopus = octopus
        self._mariadb = mariadb
        self.meters = meters
        self.region_code = region_code

    def refresh_meters(self) -> None:
        pass

    def persist_agreement(self, meter: Meter, agreements: list[Agreement]) -> None:
        self._mariadb.write_agreement(meter, agreements)

    def fetch_products(self) -> list[Product]:
        return self._octopus.get_products()

    def is_product_available_in_region(self, product_code: str, region: str) -> bool:
        return self._octopus.get_product_region_availability(product_code, region)

    def persist_product(self, product: Product) -> None:
        self._mariadb.write_product(product)

    def fetch_electricity_rates(
        self,
        product_code: str,
        tariff_code: str,
        period_from: datetime | None,
        period_to: datetime | None,
    ) -> list[Rate]:
        return self._octopus.get_electricity_rates(
            product_code, tariff_code, period_from, period_to
        )

    def fetch_gas_rates(
        self,
        product_code: str,
        tariff_code: str,
        period_from: datetime | None,
        period_to: datetime | None,
    ) -> list[Rate]:
        return self._octopus.get_gas_rates(
            product_code, tariff_code, period_from, period_to
        )

    def persist_rate(self, product_code: str, region: str, rates: list[Rate]) -> None:
        self._mariadb.write_product_rate(product_code, region, rates)

    def fetch_electricity_tariff_code(
        self, product_code: str, region: str
    ) -> str | None:
        return self._octopus.get_electricity_tariff_code(product_code, region)


def _make_electricity_meter() -> Electricity:
    return Electricity(
        mpan="1234567890123",
        serial_number="00A1234567",
        agreements=[
            Agreement(
                tariff_code="E-1R-VAR-22-11-01-A",
                valid_from=datetime(2022, 11, 1, tzinfo=UTC),
                valid_to=None,
            )
        ],
    )


def _make_electricity_meter_with_zero_width_agreement() -> Electricity:
    return Electricity(
        mpan="1234567890123",
        serial_number="00A1234567",
        agreements=[
            Agreement(
                tariff_code="E-1R-VAR-22-11-01-C",
                valid_from=datetime(2025, 5, 23, 23, tzinfo=UTC),
                valid_to=datetime(2025, 5, 23, 23, tzinfo=UTC),
            )
        ],
    )


def _make_gas_meter() -> Gas:
    return Gas(
        mprn="9876543210987",
        serial_number="00G7654321",
        agreements=[
            Agreement(
                tariff_code="G-1R-VAR-22-11-01-A",
                valid_from=datetime(2022, 11, 1, tzinfo=UTC),
                valid_to=None,
            )
        ],
    )


def _make_gas_meter_with_zero_width_agreement() -> Gas:
    return Gas(
        mprn="9876543210987",
        serial_number="00G7654321",
        agreements=[
            Agreement(
                tariff_code="G-1R-VAR-22-11-01-C",
                valid_from=datetime(2025, 5, 23, 23, tzinfo=UTC),
                valid_to=datetime(2025, 5, 23, 23, tzinfo=UTC),
            )
        ],
    )


def _make_source(
    mariadb_client: MariaDBClient, meters: list[Meter], region_code: str = "H"
) -> _RealPricingSource:
    octopus = OctopusEnergyAPIClient(
        OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
    )
    return _RealPricingSource(octopus, mariadb_client, meters, region_code)


def _mock_electricity_rate_endpoints(
    product_code: str = "VAR-22-11-01", tariff_code: str = "E-1R-VAR-22-11-01-A"
) -> None:
    responses.add(
        responses.GET,
        f"https://api.octopus.energy/v1/products/{product_code}/electricity-tariffs/"
        f"{tariff_code}/standard-unit-rates/",
        json={
            "results": [
                {
                    "value_inc_vat": 24.53,
                    "valid_from": "2022-11-01T00:00:00Z",
                    "valid_to": "2022-11-01T00:30:00Z",
                }
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"https://api.octopus.energy/v1/products/{product_code}/electricity-tariffs/"
        f"{tariff_code}/standing-charges/",
        json={
            "results": [
                {
                    "value_inc_vat": 48.20,
                    "valid_from": "2022-11-01T00:00:00Z",
                    "valid_to": None,
                }
            ],
            "next": None,
        },
        status=200,
    )


def _mock_gas_rate_endpoints(
    product_code: str = "VAR-22-11-01", tariff_code: str = "G-1R-VAR-22-11-01-A"
) -> None:
    responses.add(
        responses.GET,
        f"https://api.octopus.energy/v1/products/{product_code}/gas-tariffs/"
        f"{tariff_code}/standard-unit-rates/",
        json={
            "results": [
                {
                    "value_inc_vat": 6.89,
                    "valid_from": "2022-11-01T00:00:00Z",
                    "valid_to": "2022-11-02T00:00:00Z",
                }
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"https://api.octopus.energy/v1/products/{product_code}/gas-tariffs/"
        f"{tariff_code}/standing-charges/",
        json={
            "results": [
                {
                    "value_inc_vat": 29.11,
                    "valid_from": "2022-11-01T00:00:00Z",
                    "valid_to": None,
                }
            ],
            "next": None,
        },
        status=200,
    )


@responses.activate
def test_refresh_persists_every_meters_agreements(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )
    _mock_electricity_rate_endpoints()
    _mock_gas_rate_endpoints()
    electricity_meter = _make_electricity_meter()
    gas_meter = _make_gas_meter()
    source = _make_source(mariadb_client, [electricity_meter, gas_meter])

    PricingRetriever(source, RETENTION_DAYS).refresh()

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.agreement).all()

    assert len(stored) == 2
    assert {row.energy for row in stored} == {"E", "G"}


@responses.activate
def test_refresh_persists_products_available_in_the_account_s_region(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT,
        json={
            "results": [
                {
                    "code": "VAR-22-11-01",
                    "display_name": "Flexible Octopus",
                    "direction": "IMPORT",
                },
                {
                    "code": "AGILE-24-10-01",
                    "display_name": "Agile Octopus",
                    "direction": "IMPORT",
                },
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT + "VAR-22-11-01/",
        json={
            "single_register_electricity_tariffs": {
                "H": {"direct_debit_monthly": {"code": "E-1R-VAR-22-11-01-H"}}
            }
        },
        status=200,
    )
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT + "AGILE-24-10-01/",
        json={"single_register_electricity_tariffs": {}},
        status=200,
    )
    _mock_electricity_rate_endpoints(
        product_code="VAR-22-11-01", tariff_code="E-1R-VAR-22-11-01-H"
    )
    source = _make_source(mariadb_client, [])

    PricingRetriever(source, RETENTION_DAYS).refresh()

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product).all()

    assert [row.product_code for row in stored] == ["VAR-22-11-01"]


@responses.activate
def test_refresh_persists_the_account_s_own_product_electricity_rates(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )
    _mock_electricity_rate_endpoints()
    electricity_meter = _make_electricity_meter()
    source = _make_source(mariadb_client, [electricity_meter])

    PricingRetriever(source, RETENTION_DAYS).refresh()

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product_rate).all()

    assert len(stored) == 1
    assert stored[0].product_code == "VAR-22-11-01"
    assert stored[0].region == "H"


@responses.activate
def test_refresh_persists_gas_rates_for_the_account_s_own_product_in_the_same_shape_as_electricity(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )
    _mock_gas_rate_endpoints()
    gas_meter = _make_gas_meter()
    source = _make_source(mariadb_client, [gas_meter])

    PricingRetriever(source, RETENTION_DAYS).refresh()

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product_rate).all()

    assert len(stored) == 1
    assert stored[0].product_code == "VAR-22-11-01"
    assert stored[0].region == "H"
    assert stored[0].unit_rate == Decimal("6.89")
    assert stored[0].standing_charge == Decimal("29.11")


@responses.activate
def test_refresh_does_not_persist_export_products(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT,
        json={
            "results": [
                {
                    "code": "VAR-22-11-01",
                    "display_name": "Flexible Octopus",
                    "direction": "IMPORT",
                },
                {
                    "code": "OUTGOING-24-10-01",
                    "display_name": "Outgoing Octopus",
                    "direction": "EXPORT",
                },
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT + "VAR-22-11-01/",
        json={
            "single_register_electricity_tariffs": {
                "H": {"direct_debit_monthly": {"code": "E-1R-VAR-22-11-01-H"}}
            }
        },
        status=200,
    )
    _mock_electricity_rate_endpoints(
        product_code="VAR-22-11-01", tariff_code="E-1R-VAR-22-11-01-H"
    )
    source = _make_source(mariadb_client, [])

    PricingRetriever(source, RETENTION_DAYS).refresh()

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product).all()

    assert [row.product_code for row in stored] == ["VAR-22-11-01"]


@responses.activate
def test_refresh_persists_rates_for_every_catalogued_electricity_product(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT,
        json={
            "results": [
                {
                    "code": "AGILE-24-10-01",
                    "display_name": "Agile Octopus",
                    "direction": "IMPORT",
                }
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT + "AGILE-24-10-01/",
        json={
            "single_register_electricity_tariffs": {
                "H": {"direct_debit_monthly": {"code": "E-1R-AGILE-24-10-01-H"}}
            }
        },
        status=200,
    )
    _mock_electricity_rate_endpoints(
        product_code="AGILE-24-10-01", tariff_code="E-1R-AGILE-24-10-01-H"
    )
    source = _make_source(mariadb_client, [])

    PricingRetriever(source, RETENTION_DAYS).refresh()

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product_rate).all()

    assert len(stored) == 1
    assert stored[0].product_code == "AGILE-24-10-01"


@responses.activate
def test_refresh_skips_a_product_with_no_published_rate_for_the_region_without_crashing(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT,
        json={
            "results": [
                {
                    "code": "FIXED-24-10-01",
                    "display_name": "Fixed Octopus",
                    "direction": "IMPORT",
                }
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT + "FIXED-24-10-01/",
        json={"single_register_electricity_tariffs": {}},
        status=200,
    )
    source = _make_source(mariadb_client, [])

    PricingRetriever(source, RETENTION_DAYS).refresh()

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product_rate).all()

    assert stored == []


@responses.activate
def test_refresh_skips_a_dual_register_only_product_without_crashing(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT,
        json={
            "results": [
                {
                    "code": "ECO7-24-10-01",
                    "display_name": "Economy 7 Octopus",
                    "direction": "IMPORT",
                }
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT + "ECO7-24-10-01/",
        json={
            "single_register_electricity_tariffs": {},
            "dual_register_electricity_tariffs": {
                "H": {"direct_debit_monthly": {"code": "E-2R-ECO7-24-10-01-H"}}
            },
        },
        status=200,
    )
    source = _make_source(mariadb_client, [])

    PricingRetriever(source, RETENTION_DAYS).refresh()

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product_rate).all()

    assert stored == []


@responses.activate
def test_a_failing_agreement_s_rate_fetch_is_skipped_without_blocking_others(
    mariadb_client: MariaDBClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )
    responses.add(
        responses.GET,
        "https://api.octopus.energy/v1/products/VAR-22-11-01/electricity-tariffs/"
        "E-1R-VAR-22-11-01-A/standard-unit-rates/",
        json={"detail": "Bad request"},
        status=400,
    )
    _mock_gas_rate_endpoints()
    electricity_meter = _make_electricity_meter()
    gas_meter = _make_gas_meter()
    source = _make_source(mariadb_client, [electricity_meter, gas_meter])

    with caplog.at_level(logging.WARNING):
        PricingRetriever(source, RETENTION_DAYS).refresh()

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product_rate).all()

    assert len(stored) == 1
    assert stored[0].unit_rate == Decimal("6.89")
    assert any(
        "VAR-22-11-01/E-1R-VAR-22-11-01-A" in record.message
        for record in caplog.records
    )


@responses.activate
def test_a_failing_comparison_product_s_rate_fetch_is_skipped_without_blocking_others(
    mariadb_client: MariaDBClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT,
        json={
            "results": [
                {
                    "code": "VAR-22-11-01",
                    "display_name": "Flexible Octopus",
                    "direction": "IMPORT",
                },
                {
                    "code": "AGILE-24-10-01",
                    "display_name": "Agile Octopus",
                    "direction": "IMPORT",
                },
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT + "VAR-22-11-01/",
        json={
            "single_register_electricity_tariffs": {
                "H": {"direct_debit_monthly": {"code": "E-1R-VAR-22-11-01-H"}}
            }
        },
        status=200,
    )
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT + "AGILE-24-10-01/",
        json={
            "single_register_electricity_tariffs": {
                "H": {"direct_debit_monthly": {"code": "E-1R-AGILE-24-10-01-H"}}
            }
        },
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.octopus.energy/v1/products/VAR-22-11-01/electricity-tariffs/"
        "E-1R-VAR-22-11-01-H/standard-unit-rates/",
        json={"detail": "Bad request"},
        status=400,
    )
    _mock_electricity_rate_endpoints(
        product_code="AGILE-24-10-01", tariff_code="E-1R-AGILE-24-10-01-H"
    )
    source = _make_source(mariadb_client, [])

    with caplog.at_level(logging.WARNING):
        PricingRetriever(source, RETENTION_DAYS).refresh()

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product_rate).all()

    assert len(stored) == 1
    assert stored[0].product_code == "AGILE-24-10-01"
    assert any(
        "VAR-22-11-01/E-1R-VAR-22-11-01-H" in record.message
        for record in caplog.records
    )


@responses.activate
def test_refresh_does_not_refetch_the_account_s_own_product_during_the_comparison_pass(
    mariadb_client: MariaDBClient,
) -> None:
    """The account's own product also appears in the general catalogue. Its
    rates must come only from the agreement's actual tariff_code (the
    own-product sync), never re-fetched via an arbitrarily-picked billing
    method during the comparison-rates pass — that would risk upserting a
    different billing method's numbers over the accurate rate, since
    product_rate rows are keyed by product_code/region/valid_from, not
    tariff_code."""
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT,
        json={
            "results": [
                {
                    "code": "VAR-22-11-01",
                    "display_name": "Flexible Octopus",
                    "direction": "IMPORT",
                }
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT + "VAR-22-11-01/",
        json={
            "single_register_electricity_tariffs": {
                "H": {"direct_debit_monthly": {"code": "E-1R-VAR-22-11-01-H"}}
            }
        },
        status=200,
    )
    _mock_electricity_rate_endpoints(
        product_code="VAR-22-11-01", tariff_code="E-1R-VAR-22-11-01-A"
    )
    electricity_meter = _make_electricity_meter()
    source = _make_source(mariadb_client, [electricity_meter])

    # The product-detail endpoint above is only ever hit once, by
    # _sync_product_catalogue's availability check. If the comparison pass
    # also tried to look up VAR-22-11-01's tariff code, it would fetch
    # rates for the arbitrary "-H" billing method — but no rate endpoints
    # are mocked for that tariff_code, so a connection error would occur.
    PricingRetriever(source, RETENTION_DAYS).refresh()

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product_rate).all()

    assert len(stored) == 1
    assert stored[0].unit_rate == Decimal("24.53")


@responses.activate
def test_refresh_skips_the_rate_fetch_for_a_zero_width_electricity_agreement(
    mariadb_client: MariaDBClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )
    _mock_electricity_rate_endpoints(
        product_code="VAR-22-11-01", tariff_code="E-1R-VAR-22-11-01-C"
    )
    electricity_meter = _make_electricity_meter_with_zero_width_agreement()
    source = _make_source(mariadb_client, [electricity_meter])

    with caplog.at_level(logging.DEBUG, logger="octopus-monitor"):
        PricingRetriever(source, RETENTION_DAYS).refresh()

    rate_calls = [
        call
        for call in responses.calls
        if "electricity-tariffs/E-1R-VAR-22-11-01-C" in call.request.url
    ]
    assert rate_calls == []
    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product_rate).all()
    assert stored == []
    assert any(
        record.levelno == logging.DEBUG
        and "VAR-22-11-01/E-1R-VAR-22-11-01-C" in record.message
        and "zero or negative-width" in record.message
        for record in caplog.records
    )


@responses.activate
def test_refresh_skips_the_rate_fetch_for_a_zero_width_gas_agreement(
    mariadb_client: MariaDBClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )
    _mock_gas_rate_endpoints(
        product_code="VAR-22-11-01", tariff_code="G-1R-VAR-22-11-01-C"
    )
    gas_meter = _make_gas_meter_with_zero_width_agreement()
    source = _make_source(mariadb_client, [gas_meter])

    with caplog.at_level(logging.DEBUG, logger="octopus-monitor"):
        PricingRetriever(source, RETENTION_DAYS).refresh()

    rate_calls = [
        call
        for call in responses.calls
        if "gas-tariffs/G-1R-VAR-22-11-01-C" in call.request.url
    ]
    assert rate_calls == []
    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product_rate).all()
    assert stored == []
    assert any(
        record.levelno == logging.DEBUG
        and "VAR-22-11-01/G-1R-VAR-22-11-01-C" in record.message
        and "zero or negative-width" in record.message
        for record in caplog.records
    )


@responses.activate
def test_refresh_still_excludes_a_zero_width_agreement_s_product_from_comparison_sync(
    mariadb_client: MariaDBClient,
) -> None:
    """A zero-width agreement's own rate fetch is skipped, but its product
    code must still count as "owned" so the comparison pass doesn't treat it
    as a general-catalogue product and fetch rates for it under an
    arbitrarily-picked billing method."""
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT,
        json={
            "results": [
                {
                    "code": "VAR-22-11-01",
                    "display_name": "Flexible Octopus",
                    "direction": "IMPORT",
                }
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT + "VAR-22-11-01/",
        json={
            "single_register_electricity_tariffs": {
                "H": {"direct_debit_monthly": {"code": "E-1R-VAR-22-11-01-H"}}
            }
        },
        status=200,
    )
    electricity_meter = _make_electricity_meter_with_zero_width_agreement()
    source = _make_source(mariadb_client, [electricity_meter])

    PricingRetriever(source, RETENTION_DAYS).refresh()

    product_detail_calls = [
        call
        for call in responses.calls
        if call.request.url.startswith(PRODUCTS_ENDPOINT + "VAR-22-11-01/")
    ]
    assert len(product_detail_calls) == 1

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product_rate).all()
    assert stored == []


def _unit_rate_request_params(product_code: str) -> list[dict[str, str]]:
    return [
        dict(call.request.params)
        for call in responses.calls
        if f"/products/{product_code}/" in call.request.url
        and "standard-unit-rates" in call.request.url
    ]


@responses.activate
def test_an_open_own_agreement_from_long_ago_is_requested_from_the_window_start(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )
    _mock_electricity_rate_endpoints()
    source = _make_source(mariadb_client, [_make_electricity_meter()])

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    [params] = _unit_rate_request_params("VAR-22-11-01")
    assert params.get("period_from") == WINDOW_START_Z
    assert "period_to" not in params


def _electricity_meter_with(
    valid_from: datetime, valid_to: datetime | None
) -> Electricity:
    return Electricity(
        mpan="1234567890123",
        serial_number="00A1234567",
        agreements=[
            Agreement(
                tariff_code="E-1R-VAR-22-11-01-A",
                valid_from=valid_from,
                valid_to=valid_to,
            )
        ],
    )


@responses.activate
def test_an_own_agreement_that_began_inside_the_window_is_requested_from_its_own_start(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )
    _mock_electricity_rate_endpoints()
    meter = _electricity_meter_with(datetime(2026, 1, 1, tzinfo=UTC), None)
    source = _make_source(mariadb_client, [meter])

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    [params] = _unit_rate_request_params("VAR-22-11-01")
    assert params.get("period_from") == "2026-01-01T00:00:00Z"
    assert "period_to" not in params


@responses.activate
def test_an_own_agreement_that_ended_before_the_window_is_not_requested(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )
    _mock_electricity_rate_endpoints()
    _mock_gas_rate_endpoints()
    ended = _electricity_meter_with(
        datetime(2022, 11, 1, tzinfo=UTC), datetime(2025, 6, 1, tzinfo=UTC)
    )
    source = _make_source(mariadb_client, [ended, _make_gas_meter()])

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    requested_urls = [call.request.url for call in responses.calls]
    assert not any("electricity-tariffs" in url for url in requested_urls)
    assert any(
        "gas-tariffs" in url and "standard-unit-rates" in url for url in requested_urls
    )


@pytest.mark.parametrize(
    ("ends_after_window_start", "requested"),
    [(timedelta(0), False), (timedelta(minutes=1), True)],
)
@responses.activate
def test_an_own_agreement_ending_at_the_window_start_is_not_requested_but_one_ending_after_it_is(
    mariadb_client: MariaDBClient, ends_after_window_start: timedelta, requested: bool
) -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )
    _mock_electricity_rate_endpoints()
    window_start = REFRESHED_AT - timedelta(days=RETENTION_DAYS)
    meter = _electricity_meter_with(
        datetime(2022, 11, 1, tzinfo=UTC), window_start + ends_after_window_start
    )
    source = _make_source(mariadb_client, [meter])

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    assert bool(_unit_rate_request_params("VAR-22-11-01")) is requested


@responses.activate
def test_an_own_agreement_that_ended_inside_the_window_is_requested_up_to_its_end(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )
    _mock_electricity_rate_endpoints()
    meter = _electricity_meter_with(
        datetime(2022, 11, 1, tzinfo=UTC), datetime(2026, 1, 5, tzinfo=UTC)
    )
    source = _make_source(mariadb_client, [meter])

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    [params] = _unit_rate_request_params("VAR-22-11-01")
    assert params.get("period_from") == WINDOW_START_Z
    assert params.get("period_to") == "2026-01-05T00:00:00Z"


@responses.activate
def test_a_comparison_product_is_requested_from_the_window_start(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT,
        json={
            "results": [
                {
                    "code": "AGILE-24-10-01",
                    "display_name": "Agile Octopus",
                    "direction": "IMPORT",
                }
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT + "AGILE-24-10-01/",
        json={
            "single_register_electricity_tariffs": {
                "H": {"direct_debit_monthly": {"code": "E-1R-AGILE-24-10-01-H"}}
            }
        },
        status=200,
    )
    _mock_electricity_rate_endpoints(
        product_code="AGILE-24-10-01", tariff_code="E-1R-AGILE-24-10-01-H"
    )
    source = _make_source(mariadb_client, [])

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    [params] = _unit_rate_request_params("AGILE-24-10-01")
    assert params.get("period_from") == WINDOW_START_Z
    assert "period_to" not in params


@responses.activate
def test_a_rate_in_force_since_before_the_window_is_stored(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET, PRODUCTS_ENDPOINT, json={"results": [], "next": None}, status=200
    )
    for endpoint, value in (("standard-unit-rates", 24.53), ("standing-charges", 48.2)):
        responses.add(
            responses.GET,
            PRODUCTS_ENDPOINT + "VAR-22-11-01/electricity-tariffs/"
            f"E-1R-VAR-22-11-01-A/{endpoint}/",
            json={
                "results": [
                    {
                        "value_inc_vat": value,
                        "valid_from": "2025-06-01T00:00:00Z",
                        "valid_to": None,
                    }
                ],
                "next": None,
            },
            status=200,
        )
    source = _make_source(mariadb_client, [_make_electricity_meter()])

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.product_rate).all()
    assert len(stored) == 1
    assert stored[0].valid_from == datetime(2025, 6, 1, tzinfo=UTC).replace(tzinfo=None)
    assert stored[0].valid_to is None

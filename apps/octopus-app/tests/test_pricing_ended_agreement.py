from datetime import UTC, datetime

import responses
from tests.test_pricing_retrieval import (
    PRODUCTS_ENDPOINT,
    REFRESHED_AT,
    RETENTION_DAYS,
    _make_source,
    _mock_electricity_rate_endpoints,
    _unit_rate_request_params,
)

from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.octopus.model import Agreement, Electricity
from octopus_app.data.pricing import PricingRetriever


@responses.activate
def test_a_product_whose_agreement_has_ended_is_still_fetched_as_a_comparison_product(
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
        product_code="AGILE-24-10-01", tariff_code="E-1R-AGILE-24-10-01-A"
    )
    ended_agile = Electricity(
        mpan="1234567890123",
        serial_number="00A1234567",
        agreements=[
            Agreement(
                tariff_code="E-1R-AGILE-24-10-01-A",
                valid_from=datetime(2025, 1, 1, tzinfo=UTC),
                valid_to=datetime(2026, 1, 10, tzinfo=UTC),
            )
        ],
    )
    source = _make_source(mariadb_client, [ended_agile])

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    # Fetched with the agreement's own tariff code (not the arbitrary one the
    # catalogue lists), open-ended, and only from where the agreement ended
    # so the rates already stored for it are not overwritten.
    [open_ended] = [
        params
        for params in _unit_rate_request_params("AGILE-24-10-01")
        if "period_to" not in params
    ]
    assert open_ended.get("period_from") == "2026-01-10T00:00:00Z"
    rate_urls = [
        call.request.url
        for call in responses.calls
        if "standard-unit-rates" in call.request.url
    ]
    assert all("E-1R-AGILE-24-10-01-A" in url for url in rate_urls)

from datetime import UTC, datetime

import responses
from tests.test_pricing_retrieval import (PRODUCTS_ENDPOINT, REFRESHED_AT,
                                          RETENTION_DAYS, _make_source,
                                          _mock_electricity_rate_endpoints,
                                          _unit_rate_request_params)

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
    for tariff_code in ("E-1R-AGILE-24-10-01-A", "E-1R-AGILE-24-10-01-H"):
        _mock_electricity_rate_endpoints(
            product_code="AGILE-24-10-01", tariff_code=tariff_code
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

    requests = _unit_rate_request_params("AGILE-24-10-01")
    assert any("period_to" not in params for params in requests)

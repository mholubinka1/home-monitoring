from datetime import UTC, datetime

import responses
from tests.test_pricing_retrieval import (
    PRODUCTS_ENDPOINT,
    REFRESHED_AT,
    RETENTION_DAYS,
    WINDOW_START_Z,
    _make_source,
    _mock_electricity_rate_endpoints,
    _unit_rate_request_params,
)

from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.octopus.model import Agreement, Electricity
from octopus_app.data.pricing import PricingRetriever

AGILE_PRODUCT_CODE = "AGILE-24-10-01"
AGILE_AGREEMENT_TARIFF_CODE = "E-1R-AGILE-24-10-01-A"
CATALOGUE_TARIFF_CODE = "E-1R-AGILE-24-10-01-H"


def _mock_agile_catalogue_and_rates() -> None:
    responses.add(
        responses.GET,
        PRODUCTS_ENDPOINT,
        json={
            "results": [
                {
                    "code": AGILE_PRODUCT_CODE,
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
        PRODUCTS_ENDPOINT + AGILE_PRODUCT_CODE + "/",
        json={
            "single_register_electricity_tariffs": {
                "H": {"direct_debit_monthly": {"code": CATALOGUE_TARIFF_CODE}}
            }
        },
        status=200,
    )
    _mock_electricity_rate_endpoints(
        product_code=AGILE_PRODUCT_CODE, tariff_code=AGILE_AGREEMENT_TARIFF_CODE
    )


def _agile_agreement(valid_from: datetime, valid_to: datetime | None) -> Agreement:
    return Agreement(
        tariff_code=AGILE_AGREEMENT_TARIFF_CODE,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def _meter(*agreements: Agreement) -> Electricity:
    return Electricity(
        mpan="1234567890123",
        serial_number="00A1234567",
        agreements=list(agreements),
    )


def _open_ended_requests() -> list[dict[str, str]]:
    return [
        params
        for params in _unit_rate_request_params(AGILE_PRODUCT_CODE)
        if "period_to" not in params
    ]


@responses.activate
def test_a_product_whose_agreement_has_ended_is_still_fetched_as_a_comparison_product(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_agile_catalogue_and_rates()
    ended_agile = _meter(
        _agile_agreement(
            datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 10, tzinfo=UTC)
        )
    )
    source = _make_source(mariadb_client, [ended_agile])

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    # Fetched with the agreement's own tariff code (not the arbitrary one the
    # catalogue lists), open-ended, and only from where the agreement ended
    # so the rates already stored for it are not overwritten.
    [open_ended] = _open_ended_requests()
    assert open_ended.get("period_from") == "2026-01-10T00:00:00Z"
    rate_urls = [
        call.request.url
        for call in responses.calls
        if "standard-unit-rates" in call.request.url
    ]
    assert all(AGILE_AGREEMENT_TARIFF_CODE in url for url in rate_urls)


@responses.activate
def test_an_agreement_that_ended_before_the_window_is_fetched_from_the_window_start(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_agile_catalogue_and_rates()
    long_ended = _meter(
        _agile_agreement(
            datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 6, 1, tzinfo=UTC)
        )
    )
    source = _make_source(mariadb_client, [long_ended])

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    [open_ended] = _open_ended_requests()
    assert open_ended.get("period_from") == WINDOW_START_Z


@responses.activate
def test_the_latest_ending_agreement_of_a_product_sets_where_the_fetch_starts(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_agile_catalogue_and_rates()
    two_terms = _meter(
        _agile_agreement(
            datetime(2024, 1, 1, tzinfo=UTC), datetime(2026, 1, 5, tzinfo=UTC)
        ),
        _agile_agreement(
            datetime(2026, 1, 5, tzinfo=UTC), datetime(2026, 1, 10, tzinfo=UTC)
        ),
    )
    source = _make_source(mariadb_client, [two_terms])

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    [open_ended] = _open_ended_requests()
    assert open_ended.get("period_from") == "2026-01-10T00:00:00Z"


@responses.activate
def test_a_product_with_a_live_agreement_is_not_fetched_again_as_a_comparison(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_agile_catalogue_and_rates()
    renewed = _meter(
        _agile_agreement(
            datetime(2025, 1, 1, tzinfo=UTC), datetime(2026, 1, 10, tzinfo=UTC)
        ),
        _agile_agreement(datetime(2026, 1, 10, tzinfo=UTC), None),
    )
    source = _make_source(mariadb_client, [renewed])

    PricingRetriever(source, RETENTION_DAYS).refresh(as_of=REFRESHED_AT)

    # One own-agreement fetch per term; a comparison fetch would add a third
    # and risk overwriting the live term's accurate rates.
    assert len(_unit_rate_request_params(AGILE_PRODUCT_CODE)) == 2

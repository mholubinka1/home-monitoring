from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from octopus_app.common.config import OctopusAPISettings
from octopus_app.common.exceptions import ArgumentError
from octopus_app.data.octopus.api import OctopusEnergyAPIClient

PRODUCT_CODE = "AGILE-24-10-01"
TARIFF_CODE = "E-1R-AGILE-24-10-01-H"
UNIT_RATES_ENDPOINT = (
    f"https://api.octopus.energy/v1/products/{PRODUCT_CODE}/electricity-tariffs/"
    f"{TARIFF_CODE}/standard-unit-rates/"
)
STANDING_CHARGES_ENDPOINT = (
    f"https://api.octopus.energy/v1/products/{PRODUCT_CODE}/electricity-tariffs/"
    f"{TARIFF_CODE}/standing-charges/"
)


def _octopus() -> OctopusEnergyAPIClient:
    return OctopusEnergyAPIClient(
        OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
    )


@responses.activate
def test_half_hourly_unit_rates_are_paired_with_the_standing_charge_in_effect() -> None:
    responses.add(
        responses.GET,
        UNIT_RATES_ENDPOINT,
        json={
            "results": [
                {
                    "value_inc_vat": 24.53,
                    "valid_from": "2026-01-01T00:00:00Z",
                    "valid_to": "2026-01-01T00:30:00Z",
                },
                {
                    "value_inc_vat": 26.10,
                    "valid_from": "2026-01-01T00:30:00Z",
                    "valid_to": "2026-01-01T01:00:00Z",
                },
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        STANDING_CHARGES_ENDPOINT,
        json={
            "results": [
                {
                    "value_inc_vat": 48.20,
                    "valid_from": "2026-01-01T00:00:00Z",
                    "valid_to": None,
                }
            ],
            "next": None,
        },
        status=200,
    )

    rates = _octopus().get_electricity_rates(PRODUCT_CODE, TARIFF_CODE)

    assert len(rates) == 2
    assert rates[0].unit_rate == Decimal("24.53")
    assert rates[0].standing_charge == Decimal("48.20")
    assert rates[0].valid_from == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert rates[0].valid_to == datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
    assert rates[1].unit_rate == Decimal("26.10")
    assert rates[1].standing_charge == Decimal("48.20")


@responses.activate
def test_a_unit_rate_window_with_no_covering_standing_charge_is_skipped_not_crashed() -> (
    None
):
    responses.add(
        responses.GET,
        UNIT_RATES_ENDPOINT,
        json={
            "results": [
                {
                    "value_inc_vat": 24.53,
                    "valid_from": "2026-01-01T00:00:00Z",
                    "valid_to": "2026-01-01T00:30:00Z",
                },
                {
                    "value_inc_vat": 26.10,
                    "valid_from": "2026-01-01T00:30:00Z",
                    "valid_to": "2026-01-01T01:00:00Z",
                },
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        STANDING_CHARGES_ENDPOINT,
        json={
            "results": [
                {
                    "value_inc_vat": 48.20,
                    "valid_from": "2026-01-01T00:00:00Z",
                    "valid_to": "2026-01-01T00:30:00Z",
                }
            ],
            "next": None,
        },
        status=200,
    )

    rates = _octopus().get_electricity_rates(PRODUCT_CODE, TARIFF_CODE)

    assert len(rates) == 1
    assert rates[0].valid_from == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


@responses.activate
def test_paginated_unit_rates_are_followed_to_completion() -> None:
    next_page_endpoint = UNIT_RATES_ENDPOINT + "?page=2"
    responses.add(
        responses.GET,
        UNIT_RATES_ENDPOINT,
        json={
            "results": [
                {
                    "value_inc_vat": 24.53,
                    "valid_from": "2026-01-01T00:00:00Z",
                    "valid_to": "2026-01-01T00:30:00Z",
                }
            ],
            "next": next_page_endpoint,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        next_page_endpoint,
        json={
            "results": [
                {
                    "value_inc_vat": 26.10,
                    "valid_from": "2026-01-01T00:30:00Z",
                    "valid_to": "2026-01-01T01:00:00Z",
                }
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        STANDING_CHARGES_ENDPOINT,
        json={
            "results": [
                {
                    "value_inc_vat": 48.20,
                    "valid_from": "2026-01-01T00:00:00Z",
                    "valid_to": None,
                }
            ],
            "next": None,
        },
        status=200,
    )

    rates = _octopus().get_electricity_rates(PRODUCT_CODE, TARIFF_CODE)

    assert [r.unit_rate for r in rates] == [Decimal("24.53"), Decimal("26.10")]


@responses.activate
def test_a_non_utc_period_is_normalized_to_utc_z_format_in_the_request() -> None:
    responses.add(
        responses.GET,
        UNIT_RATES_ENDPOINT,
        json={
            "results": [
                {
                    "value_inc_vat": 24.53,
                    "valid_from": "2024-01-06T00:00:00Z",
                    "valid_to": "2024-01-06T00:30:00Z",
                }
            ],
            "next": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        STANDING_CHARGES_ENDPOINT,
        json={
            "results": [
                {
                    "value_inc_vat": 48.20,
                    "valid_from": "2024-01-06T00:00:00Z",
                    "valid_to": None,
                }
            ],
            "next": None,
        },
        status=200,
    )
    bst = timezone(timedelta(hours=1))
    period_from = datetime(2024, 1, 6, 0, 0, 0, tzinfo=bst)
    period_to = datetime(2024, 5, 24, 0, 0, 0, tzinfo=bst)

    rates = _octopus().get_electricity_rates(
        PRODUCT_CODE, TARIFF_CODE, period_from=period_from, period_to=period_to
    )

    unit_rates_calls = [
        call
        for call in responses.calls
        if call.request.url and call.request.url.startswith(UNIT_RATES_ENDPOINT)
    ]
    assert len(unit_rates_calls) == 1
    query = parse_qs(urlparse(unit_rates_calls[0].request.url).query)
    assert query["period_from"] == ["2024-01-05T23:00:00Z"]
    assert query["period_to"] == ["2024-05-23T23:00:00Z"]
    assert len(rates) == 1
    assert rates[0].unit_rate == Decimal("24.53")
    assert rates[0].standing_charge == Decimal("48.20")


def test_a_naive_period_is_rejected_rather_than_silently_using_local_time() -> None:
    naive_period_from = datetime(2024, 1, 6)  # naive is the point

    with pytest.raises(ArgumentError, match="timezone-aware"):
        _octopus().get_electricity_rates(
            PRODUCT_CODE, TARIFF_CODE, period_from=naive_period_from
        )

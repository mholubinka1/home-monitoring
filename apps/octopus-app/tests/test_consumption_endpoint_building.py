from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
import responses

from octopus_app.common.config import OctopusAPISettings
from octopus_app.common.exceptions import ArgumentError
from octopus_app.data.octopus.api import OctopusEnergyAPIClient
from octopus_app.data.octopus.model import Agreement, Electricity, Gas

CONSUMPTION_ENDPOINT = (
    "https://api.octopus.energy/v1/electricity-meter-points/"
    "1234567890123/meters/00A1234567/consumption/"
)


def _octopus() -> OctopusEnergyAPIClient:
    return OctopusEnergyAPIClient(
        OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test")
    )


def _meter() -> Electricity:
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


@responses.activate
def test_a_non_utc_period_is_normalized_to_utc_z_format_in_the_request() -> None:
    responses.add(
        responses.GET,
        CONSUMPTION_ENDPOINT,
        json={"results": [], "next": None},
        status=200,
    )
    bst = timezone(timedelta(hours=1))
    period_from = datetime(2024, 6, 6, 0, 0, 0, tzinfo=bst)
    period_to = datetime(2024, 6, 7, 0, 0, 0, tzinfo=bst)

    _octopus().get_consumption(_meter(), period_from, period_to)

    assert len(responses.calls) == 1
    query = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert query["period_from"] == ["2024-06-05T23:00:00Z"]
    assert query["period_to"] == ["2024-06-06T23:00:00Z"]


def test_a_naive_period_is_rejected_rather_than_silently_using_local_time() -> None:
    naive_period_from = datetime(2024, 1, 6)  # noqa: DTZ001 -- naive is the point

    with pytest.raises(ArgumentError, match="timezone-aware"):
        _octopus().get_consumption(_meter(), naive_period_from)


GAS_CONSUMPTION_ENDPOINT = (
    "https://api.octopus.energy/v1/gas-meter-points/9876543210/meters/G4A1234567/"
    "consumption/"
)


def _gas_meter() -> Gas:
    return Gas(
        mprn="9876543210",
        serial_number="G4A1234567",
        agreements=[
            Agreement(
                tariff_code="G-1R-VAR-22-11-01-A",
                valid_from=datetime(2022, 11, 1, tzinfo=UTC),
                valid_to=None,
            )
        ],
    )


@responses.activate
@pytest.mark.parametrize(
    ("meter", "endpoint"),
    [
        (_meter(), CONSUMPTION_ENDPOINT),
        (_gas_meter(), GAS_CONSUMPTION_ENDPOINT),
    ],
    ids=["electricity", "gas"],
)
def test_consumption_is_requested_newest_first_in_pages_of_100(
    meter: Electricity | Gas, endpoint: str
) -> None:
    # Octopus's ascending order (order_by=period) returns overlapping and
    # skipping pages once a window spans more than one page, so duplicated and
    # missing intervals corrupt anything built from a paged fetch (ADR-0027).
    responses.add(
        responses.GET,
        endpoint,
        json={"results": [], "next": None},
        status=200,
    )

    _octopus().get_consumption(meter, datetime(2026, 1, 1, tzinfo=UTC))

    query = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert query["order_by"] == ["-period"]
    assert query["page_size"] == ["100"]

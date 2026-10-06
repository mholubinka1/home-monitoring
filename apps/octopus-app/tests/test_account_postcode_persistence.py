import responses

from common.mariadb.model import account_postcode
from octopus_app.common.config import (
    ApplicationSettings,
    MariaDBSettings,
    OctopusAPISettings,
    RefreshSettings,
)
from octopus_app.data.base import MonitoringClient
from octopus_app.data.mysql.client import MariaDBClient

ACCOUNT_ENDPOINT = "https://api.octopus.energy/v1/accounts/A-1234ABCD"
GRID_SUPPLY_POINTS_ENDPOINT = (
    "https://api.octopus.energy/v1/industry/grid-supply-points"
)


def _account_response(postcode: str) -> dict[str, object]:
    return {
        "properties": [
            {
                "postcode": postcode,
                "address_line_1": "1 Test Street",
                "town": "Testville",
                "electricity_meter_points": [],
                "gas_meter_points": [],
            }
        ]
    }


def _settings() -> ApplicationSettings:
    return ApplicationSettings(
        octopus=OctopusAPISettings(account_number="A-1234ABCD", api_key="sk_live_test"),
        mariadb=MariaDBSettings(
            host="localhost",
            port=3306,
            # "main" (not "octopus"): must agree with the mariadb_client
            # fixture's own map -- see ADR-0025.
            database="main",
            username="test",
            password="test",
        ),
        data_refresh=RefreshSettings(refresh_interval_hours=4, retention_days=45),
    )


def _mock_octopus(postcode: str) -> None:
    responses.add(
        responses.GET, ACCOUNT_ENDPOINT, json=_account_response(postcode), status=200
    )
    responses.add(
        responses.GET,
        GRID_SUPPLY_POINTS_ENDPOINT,
        json={"results": [{"group_id": "_H"}]},
        status=200,
    )


@responses.activate
def test_starting_octopus_app_records_the_account_postcode(
    mariadb_client: MariaDBClient,
) -> None:
    _mock_octopus("AB1 2CD")

    MonitoringClient(_settings())

    assert mariadb_client.read_account_postcode() == "AB12CD"


@responses.activate
def test_starting_octopus_app_overwrites_a_previously_recorded_postcode(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_account_postcode("ZZ99ZZ")
    _mock_octopus("AB1 2CD")

    MonitoringClient(_settings())

    with mariadb_client.session_read_scope() as session:
        postcodes = [row.postcode for row in session.query(account_postcode).all()]
    assert postcodes == ["AB12CD"]

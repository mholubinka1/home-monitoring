import logging

import pytest
import requests
import responses
from weather_hourly_payloads import recent_hourly_payload

from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.weather_location import WeatherLocationUnavailableError
from hive_app.main import _build_weather_retriever

POSTCODES_IO_ENDPOINT = "https://api.postcodes.io/postcodes/AB12CD"
IPWHO_ENDPOINT = "https://ipwho.is/"
OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"

OPEN_METEO_HOURLY_RESPONSE = recent_hourly_payload(1)


@responses.activate
def test_an_ip_geolocation_reply_with_null_coordinates_is_a_failure(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        IPWHO_ENDPOINT,
        json={"success": True, "latitude": None, "longitude": None},
        status=200,
    )

    with pytest.raises(WeatherLocationUnavailableError):
        _build_weather_retriever(None, mariadb_client).refresh()

    with mariadb_client.session_read_scope() as session:
        assert session.query(model.weather_location).count() == 0


@pytest.mark.parametrize(
    "failure",
    [requests.ConnectionError("connection refused"), requests.Timeout("timed out")],
)
@responses.activate
def test_a_postcodes_io_network_failure_falls_back_to_ip_geolocation_without_logging_the_postcode(
    mariadb_client: MariaDBClient,
    caplog: pytest.LogCaptureFixture,
    failure: Exception,
) -> None:
    mariadb_client.write_account_postcode("AB12CD")
    responses.add(responses.GET, POSTCODES_IO_ENDPOINT, body=failure)
    responses.add(
        responses.GET,
        IPWHO_ENDPOINT,
        json={"success": True, "latitude": 53.4, "longitude": -2.2},
        status=200,
    )
    responses.add(
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_HOURLY_RESPONSE, status=200
    )

    with caplog.at_level(logging.DEBUG):
        _build_weather_retriever(None, mariadb_client).refresh()

    assert "latitude=53.4" in responses.calls[-1].request.url
    with mariadb_client.session_read_scope() as session:
        assert [loc.source for loc in session.query(model.weather_location)] == ["ip"]
    assert "AB12CD" not in caplog.text

import logging

import pytest
import requests
import responses

from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.weather_location import WeatherLocationUnavailableError
from hive_app.main import _build_weather_retriever

POSTCODES_IO_ENDPOINT = "https://api.postcodes.io/postcodes/AB12CD"
IPWHO_ENDPOINT = "https://ipwho.is/"
OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"

OPEN_METEO_CURRENT_RESPONSE = {
    "current": {
        "time": "2026-09-25T12:00",
        "temperature_2m": 14.5,
        "relative_humidity_2m": 72,
        "surface_pressure": 1012.3,
        "wind_speed_10m": 8.1,
        "precipitation": 0.0,
    }
}


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
        _build_weather_retriever(None, None, mariadb_client).refresh()

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
        responses.GET, OPEN_METEO_ENDPOINT, json=OPEN_METEO_CURRENT_RESPONSE, status=200
    )

    with caplog.at_level(logging.DEBUG):
        _build_weather_retriever(None, None, mariadb_client).refresh()

    assert "latitude=53.4" in responses.calls[-1].request.url
    with mariadb_client.session_read_scope() as session:
        assert [loc.source for loc in session.query(model.weather_location)] == ["ip"]
    assert "AB12CD" not in caplog.text

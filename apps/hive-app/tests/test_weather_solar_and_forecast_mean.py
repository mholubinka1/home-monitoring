from datetime import UTC, datetime

import pytest
import responses
from pydantic import ValidationError
from weather_hourly_payloads import hourly_payload

from hive_app.common.config import LocationSettings
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.open_meteo_client import OpenMeteoClient

OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
LONDON = LocationSettings(latitude=51.5, longitude=-0.1)
NOON = datetime(2026, 9, 25, 12, 30, tzinfo=UTC)


def _solar_payload(**overrides: list) -> dict:
    values = {
        "temperature_2m": [14.5],
        "shortwave_radiation": [312.0],
        "cloud_cover": [40],
        "sunshine_duration": [900.0],
        **overrides,
    }
    return hourly_payload(["2026-09-25T12:00"], **values)


def _client() -> OpenMeteoClient:
    return OpenMeteoClient(LONDON, clock=lambda: NOON)


@responses.activate
def test_solar_radiation_cloud_cover_and_sunshine_are_stored_with_the_observation(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.GET, OPEN_METEO_ENDPOINT, json=_solar_payload(), status=200)

    mariadb_client.write_weather_observations(_client().get_recent_observations())

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.weather_observation).one()

    assert stored.shortwave_radiation == 312.0
    assert stored.cloud_cover == 40
    assert stored.sunshine_duration == 900.0


@responses.activate
@pytest.mark.parametrize("key_present", [True, False], ids=["key null", "key absent"])
def test_an_observation_missing_sunshine_is_stored_with_null_for_it(
    mariadb_client: MariaDBClient, key_present: bool
) -> None:
    payload = _solar_payload()
    if key_present:
        payload["hourly"]["sunshine_duration"] = [None]
    else:
        del payload["hourly"]["sunshine_duration"]
    responses.add(responses.GET, OPEN_METEO_ENDPOINT, json=payload, status=200)

    mariadb_client.write_weather_observations(_client().get_recent_observations())

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.weather_observation).one()

    assert stored.sunshine_duration is None
    assert stored.shortwave_radiation == 312.0
    assert stored.cloud_cover == 40
    assert stored.temp == 14.5


@responses.activate
def test_an_open_meteo_response_with_a_non_finite_cloud_cover_is_rejected() -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json=_solar_payload(cloud_cover=[float("inf")]),
        status=200,
    )

    with pytest.raises(ValidationError, match="finite"):
        _client().get_recent_observations()


@responses.activate
def test_each_forecast_days_mean_temperature_is_stored_alongside_the_maximum(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "daily": {
                "time": ["2026-09-26", "2026-09-27"],
                "temperature_2m_max": [14.5, 12.1],
                "temperature_2m_mean": [11.2, 9.8],
            }
        },
        status=200,
    )

    mariadb_client.write_weather_forecast(OpenMeteoClient(LONDON).get_forecast())

    assert "temperature_2m_max%2Ctemperature_2m_mean" in responses.calls[0].request.url
    with mariadb_client.session_read_scope() as session:
        stored = (
            session.query(model.weather_forecast)
            .order_by(model.weather_forecast.target_date)
            .all()
        )

    assert [(day.max_temp, day.mean_temp) for day in stored] == [
        (14.5, 11.2),
        (12.1, 9.8),
    ]


@responses.activate
def test_a_forecast_without_mean_temperatures_is_stored_with_null_means(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "daily": {
                "time": ["2026-09-26", "2026-09-27"],
                "temperature_2m_max": [14.5, 12.1],
            }
        },
        status=200,
    )

    mariadb_client.write_weather_forecast(OpenMeteoClient(LONDON).get_forecast())

    with mariadb_client.session_read_scope() as session:
        stored = (
            session.query(model.weather_forecast)
            .order_by(model.weather_forecast.target_date)
            .all()
        )

    assert [(day.max_temp, day.mean_temp) for day in stored] == [
        (14.5, None),
        (12.1, None),
    ]


@responses.activate
def test_a_forecast_with_a_mismatched_length_mean_array_is_rejected() -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "daily": {
                "time": ["2026-09-26", "2026-09-27"],
                "temperature_2m_max": [14.5, 12.1],
                "temperature_2m_mean": [11.2],
            }
        },
        status=200,
    )

    with pytest.raises(ValueError, match="mismatched array lengths"):
        OpenMeteoClient(LONDON).get_forecast()

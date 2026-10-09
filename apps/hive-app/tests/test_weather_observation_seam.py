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
NOON = datetime(2026, 9, 25, 12, 30, tzinfo=UTC)


def _client() -> OpenMeteoClient:
    return OpenMeteoClient(
        LocationSettings(latitude=51.5, longitude=-0.1), clock=lambda: NOON
    )


@responses.activate
def test_an_open_meteo_observation_is_persisted_and_queryable(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json=hourly_payload(
            ["2026-09-25T12:00"],
            temperature_2m=[14.5],
            relative_humidity_2m=[72],
            surface_pressure=[1012.3],
            wind_speed_10m=[8.1],
            precipitation=[0.0],
        ),
        status=200,
    )

    observations = _client().get_recent_observations()

    # Unlike get_forecast() (which requests Europe/London for ADR-0010 day
    # bucketing), this request must stay UTC -- observed_at is a point-in-
    # time instant, not a calendar day, so forcing UTC just avoids guessing
    # an offset on the naive timestamp Open-Meteo returns.
    assert len(responses.calls) == 1
    assert "timezone=UTC" in responses.calls[0].request.url

    mariadb_client.write_weather_observations(observations)

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.weather_observation).all()

    assert len(stored) == 1
    assert stored[0].source == "open-meteo"
    assert stored[0].temp == 14.5
    assert stored[0].humidity == 72
    assert stored[0].pressure == 1012.3
    assert stored[0].wind_speed == 8.1
    assert stored[0].precipitation == 0.0


@responses.activate
def test_an_open_meteo_response_with_a_non_finite_field_is_rejected() -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json=hourly_payload(["2026-09-25T12:00"], temperature_2m=[float("inf")]),
        status=200,
    )

    with pytest.raises(ValidationError, match="finite"):
        _client().get_recent_observations()

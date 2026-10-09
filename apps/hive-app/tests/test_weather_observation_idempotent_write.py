from dataclasses import replace
from datetime import UTC, datetime

import responses
from weather_hourly_payloads import hourly_payload

from hive_app.common.config import LocationSettings
from hive_app.data.model import WeatherObservation
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.open_meteo_client import OpenMeteoClient

OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
LONDON = LocationSettings(latitude=51.5, longitude=-0.1)
LEEDS = LocationSettings(latitude=53.8, longitude=-1.55)
NOON = datetime(2026, 9, 25, 12, 30, tzinfo=UTC)


def _observe(
    temp: float, location: LocationSettings = LONDON
) -> list[WeatherObservation]:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json=hourly_payload(["2026-09-25T12:00"], temperature_2m=[temp]),
        status=200,
    )
    return OpenMeteoClient(location, clock=lambda: NOON).get_recent_observations()


def _stored(mariadb_client: MariaDBClient) -> list[model.weather_observation]:
    with mariadb_client.session_read_scope() as session:
        return session.query(model.weather_observation).all()


@responses.activate
def test_an_hour_written_twice_for_the_same_source_leaves_one_row_with_the_latest_values(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_observations(_observe(temp=14.5))
    mariadb_client.write_weather_observations(_observe(temp=15.2))

    stored = _stored(mariadb_client)

    assert len(stored) == 1
    assert stored[0].temp == 15.2


@responses.activate
def test_the_same_hour_from_two_sources_is_stored_for_each(
    mariadb_client: MariaDBClient,
) -> None:
    observations = _observe(temp=14.5)

    mariadb_client.write_weather_observations(observations)
    mariadb_client.write_weather_observations(
        [replace(observation, source="archive") for observation in observations]
    )

    assert {row.source for row in _stored(mariadb_client)} == {"open-meteo", "archive"}


@responses.activate
def test_the_same_hour_at_two_locations_is_stored_for_each(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_observations(_observe(temp=14.5, location=LONDON))
    mariadb_client.write_weather_observations(_observe(temp=9.0, location=LEEDS))

    assert len(_stored(mariadb_client)) == 2


@responses.activate
def test_an_observation_is_stored_with_the_weather_locations_key_not_a_postcode(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_observations(_observe(temp=14.5, location=LONDON))

    assert _stored(mariadb_client)[0].location == "51.50,-0.10"


@responses.activate
def test_a_location_key_never_shows_negative_zero(
    mariadb_client: MariaDBClient,
) -> None:
    greenwich = LocationSettings(latitude=51.48, longitude=-0.001)

    mariadb_client.write_weather_observations(_observe(temp=14.5, location=greenwich))

    assert _stored(mariadb_client)[0].location == "51.48,0.00"

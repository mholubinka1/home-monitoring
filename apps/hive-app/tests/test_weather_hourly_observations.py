from datetime import UTC, datetime
from typing import Any

import pytest
import responses
from pydantic import ValidationError
from weather_hourly_payloads import HOURLY_VARIABLES, hourly_payload

from hive_app.common.config import LocationSettings
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.open_meteo_client import OpenMeteoClient

OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
LONDON = LocationSettings(latitude=51.5, longitude=-0.1)
QUARTER_TO_NINE = datetime(2026, 10, 9, 8, 45, tzinfo=UTC)


def _client(now: datetime = QUARTER_TO_NINE) -> OpenMeteoClient:
    return OpenMeteoClient(LONDON, clock=lambda: now)


@responses.activate
def test_each_completed_hour_is_stored_on_the_hour_with_its_hourly_values(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json=hourly_payload(
            ["2026-10-09T06:00", "2026-10-09T07:00", "2026-10-09T08:00"]
        ),
        status=200,
    )

    mariadb_client.write_weather_observations(_client().get_recent_observations())

    with mariadb_client.session_read_scope() as session:
        stored = (
            session.query(model.weather_observation)
            .order_by(model.weather_observation.observed_at)
            .all()
        )
    assert [row.observed_at.replace(tzinfo=UTC) for row in stored] == [
        datetime(2026, 10, 9, 6, 0, tzinfo=UTC),
        datetime(2026, 10, 9, 7, 0, tzinfo=UTC),
        datetime(2026, 10, 9, 8, 0, tzinfo=UTC),
    ]
    assert [row.temp for row in stored] == [15.0, 16.0, 17.0]
    assert [row.sunshine_duration for row in stored] == [0.0, 900.0, 1800.0]
    assert {row.location for row in stored} == {"51.50,-0.10"}


@responses.activate
def test_an_hour_later_than_the_current_hour_is_not_stored() -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json=hourly_payload(
            ["2026-10-09T07:00", "2026-10-09T08:00", "2026-10-09T09:00"]
        ),
        status=200,
    )

    observations = _client().get_recent_observations()

    assert [o.observed_at for o in observations] == [
        datetime(2026, 10, 9, 7, 0, tzinfo=UTC),
        datetime(2026, 10, 9, 8, 0, tzinfo=UTC),
    ]


@responses.activate
def test_an_hour_with_every_variable_null_is_skipped_and_the_others_are_stored() -> (
    None
):
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json=hourly_payload(
            ["2026-10-09T06:00", "2026-10-09T07:00", "2026-10-09T08:00"],
            **{name: [1.0, None, 1.0] for name in HOURLY_VARIABLES},
        ),
        status=200,
    )

    observations = _client().get_recent_observations()

    assert [o.observed_at.hour for o in observations] == [6, 8]


@responses.activate
def test_an_hour_missing_one_variable_is_stored_with_null_for_it(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json=hourly_payload(
            ["2026-10-09T07:00", "2026-10-09T08:00"],
            sunshine_duration=[900.0, None],
            surface_pressure=[1012.0, None],
        ),
        status=200,
    )

    mariadb_client.write_weather_observations(_client().get_recent_observations())

    with mariadb_client.session_read_scope() as session:
        stored = (
            session.query(model.weather_observation)
            .order_by(model.weather_observation.observed_at)
            .all()
        )
    assert len(stored) == 2
    assert stored[1].sunshine_duration is None
    assert stored[1].pressure is None
    assert stored[1].temp == 16.0
    assert stored[1].humidity == 70
    assert stored[1].precipitation == 0.2


@pytest.mark.parametrize("variable", ["temperature_2m", "cloud_cover"])
@responses.activate
def test_a_non_finite_value_in_any_hour_is_rejected(variable: str) -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json=hourly_payload(
            ["2026-10-09T07:00", "2026-10-09T08:00"],
            **{variable: [10.0, float("inf")]},
        ),
        status=200,
    )

    with pytest.raises(ValidationError, match="finite"):
        _client().get_recent_observations()


@responses.activate
def test_mismatched_array_lengths_are_rejected() -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json=hourly_payload(
            ["2026-10-09T07:00", "2026-10-09T08:00"], temperature_2m=[10.0]
        ),
        status=200,
    )

    with pytest.raises(ValueError, match="mismatched array lengths"):
        _client().get_recent_observations()


def _stored(mariadb_client: MariaDBClient) -> list[model.weather_observation]:
    with mariadb_client.session_read_scope() as session:
        return (
            session.query(model.weather_observation)
            .order_by(model.weather_observation.observed_at)
            .all()
        )


def _run(
    mariadb_client: MariaDBClient,
    payload: dict[str, Any],
    location: LocationSettings = LONDON,
) -> None:
    responses.add(responses.GET, OPEN_METEO_ENDPOINT, json=payload, status=200)
    client = OpenMeteoClient(location, clock=lambda: QUARTER_TO_NINE)
    mariadb_client.write_weather_observations(client.get_recent_observations())


@responses.activate
def test_the_same_run_repeated_leaves_the_same_rows_with_the_latest_values(
    mariadb_client: MariaDBClient,
) -> None:
    hours = ["2026-10-09T06:00", "2026-10-09T07:00", "2026-10-09T08:00"]

    _run(mariadb_client, hourly_payload(hours))
    _run(
        mariadb_client,
        hourly_payload(hours, temperature_2m=[20.0, 21.0, 22.0]),
    )

    assert [row.temp for row in _stored(mariadb_client)] == [20.0, 21.0, 22.0]


@responses.activate
def test_the_same_hours_at_two_locations_are_stored_for_each(
    mariadb_client: MariaDBClient,
) -> None:
    hours = ["2026-10-09T07:00", "2026-10-09T08:00"]

    _run(mariadb_client, hourly_payload(hours))
    _run(
        mariadb_client,
        hourly_payload(hours),
        location=LocationSettings(latitude=53.8, longitude=-1.55),
    )

    assert len(_stored(mariadb_client)) == 4
    assert {row.location for row in _stored(mariadb_client)} == {
        "51.50,-0.10",
        "53.80,-1.55",
    }


@responses.activate
def test_an_hour_missed_by_an_earlier_run_is_stored_by_the_next_run(
    mariadb_client: MariaDBClient,
) -> None:
    _run(
        mariadb_client,
        hourly_payload(["2026-10-09T02:00", "2026-10-09T04:00"]),
    )
    _run(
        mariadb_client,
        hourly_payload(["2026-10-09T02:00", "2026-10-09T03:00", "2026-10-09T04:00"]),
    )

    assert [row.observed_at.hour for row in _stored(mariadb_client)] == [2, 3, 4]

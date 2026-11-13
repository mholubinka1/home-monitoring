"""The Hourly Weather Forecast: Open-Meteo's forecast temperature and sunshine
for each coming hour, stored for the room forecast (ADR-0035)."""

from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from hive_app.common.config import LocationSettings
from hive_app.data.model import WeatherForecastHour
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.open_meteo_client import OpenMeteoClient

OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
LONDON = LocationSettings(latitude=51.5, longitude=-0.1)


@responses.activate
def test_each_forecast_hour_is_stored_with_its_temperature_and_sunshine(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "hourly": {
                "time": ["2026-11-13T11:00", "2026-11-13T12:00", "2026-11-13T13:00"],
                "temperature_2m": [4.9, 6.0, 6.8],
                "shortwave_radiation": [286.0, 315.0, 299.0],
            }
        },
        status=200,
    )

    mariadb_client.write_weather_forecast_hours(
        OpenMeteoClient(LONDON).get_hourly_forecast()
    )

    with mariadb_client.session_read_scope() as session:
        stored = (
            session.query(model.weather_forecast_hour)
            .order_by(model.weather_forecast_hour.forecast_at)
            .all()
        )

    assert [
        (hour.source, hour.location, hour.forecast_at, hour.temp) for hour in stored
    ] == [
        ("open-meteo", "51.50,-0.10", datetime(2026, 11, 13, 11, 0), 4.9),
        ("open-meteo", "51.50,-0.10", datetime(2026, 11, 13, 12, 0), 6.0),
        ("open-meteo", "51.50,-0.10", datetime(2026, 11, 13, 13, 0), 6.8),
    ]
    assert [hour.shortwave_radiation for hour in stored] == [286.0, 315.0, 299.0]


@responses.activate
def test_the_request_asks_for_54_hours_of_temperature_and_sunshine_in_utc() -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={"hourly": {"time": []}},
        status=200,
    )

    OpenMeteoClient(LONDON).get_hourly_forecast()

    query = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert query["hourly"] == ["temperature_2m,shortwave_radiation"]
    assert query["forecast_hours"] == ["54"]
    assert query["timezone"] == ["UTC"]


def _forecast(temp: float) -> list[WeatherForecastHour]:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "hourly": {
                "time": ["2026-11-13T11:00"],
                "temperature_2m": [temp],
                "shortwave_radiation": [286.0],
            }
        },
        status=200,
    )
    return OpenMeteoClient(LONDON).get_hourly_forecast()


@responses.activate
def test_a_later_forecast_for_an_hour_replaces_the_stored_one(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_forecast_hours(_forecast(temp=4.9))
    mariadb_client.write_weather_forecast_hours(_forecast(temp=7.2))

    with mariadb_client.session_read_scope() as session:
        stored = session.query(model.weather_forecast_hour).all()

    assert [hour.temp for hour in stored] == [7.2]


@responses.activate
def test_an_hour_with_neither_temperature_nor_sunshine_is_not_stored() -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "hourly": {
                "time": ["2026-11-13T11:00", "2026-11-13T12:00"],
                "temperature_2m": [None, 6.0],
                "shortwave_radiation": [None, None],
            }
        },
        status=200,
    )

    hours = OpenMeteoClient(LONDON).get_hourly_forecast()

    assert [hour.forecast_at for hour in hours] == [datetime(2026, 11, 13, 12, 0)]


@responses.activate
def test_a_response_whose_arrays_differ_in_length_is_rejected() -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "hourly": {
                "time": ["2026-11-13T11:00", "2026-11-13T12:00"],
                "temperature_2m": [4.9],
                "shortwave_radiation": [286.0, 315.0],
            }
        },
        status=200,
    )

    with pytest.raises(ValueError, match="mismatched array lengths"):
        OpenMeteoClient(LONDON).get_hourly_forecast()


def test_a_database_created_before_the_hourly_forecast_gains_its_table_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"octopus": "main"})
    monkeypatch.setattr(
        "common.mariadb.client.create_engine", lambda *args, **kwargs: engine
    )

    MariaDBClient(
        MariaDBSettings(
            host="localhost",
            port=3306,
            database="main",
            username="test",
            password="test",
        )
    )

    assert "weather_forecast_hour" in inspect(engine).get_table_names()

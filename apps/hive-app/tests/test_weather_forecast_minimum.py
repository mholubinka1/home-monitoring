from datetime import date, datetime
from typing import ClassVar

import pytest
import responses
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    String,
    create_engine,
    insert,
    text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from hive_app.common.config import LocationSettings
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.open_meteo_client import OpenMeteoClient

OPEN_METEO_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
LONDON = LocationSettings(latitude=51.5, longitude=-0.1)


@responses.activate
def test_each_forecast_days_minimum_temperature_is_stored_beside_the_maximum_and_mean(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "daily": {
                "time": ["2026-09-26", "2026-09-27"],
                "temperature_2m_max": [14.5, 12.1],
                "temperature_2m_min": [6.3, 4.9],
                "temperature_2m_mean": [11.2, 9.8],
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

    assert [(day.min_temp, day.max_temp, day.mean_temp) for day in stored] == [
        (6.3, 14.5, 11.2),
        (4.9, 12.1, 9.8),
    ]


@responses.activate
def test_a_forecast_without_a_minimum_temperature_array_stores_null_minimums(
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

    with mariadb_client.session_read_scope() as session:
        stored = (
            session.query(model.weather_forecast)
            .order_by(model.weather_forecast.target_date)
            .all()
        )

    assert [(day.min_temp, day.max_temp) for day in stored] == [
        (None, 14.5),
        (None, 12.1),
    ]


_PreMinimumBase = declarative_base()


class _PreMinimumWeatherForecast(_PreMinimumBase):  # type: ignore[misc,valid-type]
    """weather_forecast as it was before the daily minimum was collected."""

    __tablename__ = "weather_forecast"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(String(50), primary_key=True)
    source = Column(String(20))
    target_date = Column(Date, nullable=False)
    max_temp = Column(Float)
    mean_temp = Column(Float)
    fetched_at = Column(DateTime, nullable=False)


def test_an_existing_forecast_table_gains_the_minimum_column_keeping_its_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"octopus": "main"})
    _PreMinimumBase.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(_PreMinimumWeatherForecast.__table__).values(
                id="open-meteo:2026-09-26",
                source="open-meteo",
                target_date=date(2026, 9, 26),
                max_temp=14.5,
                mean_temp=11.2,
                fetched_at=datetime(2026, 9, 26, 6, 0),
            )
        )
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

    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT max_temp, min_temp FROM weather_forecast")
        ).all()
    assert [tuple(row) for row in rows] == [(14.5, None)]


@responses.activate
def test_a_forecast_with_a_mismatched_length_minimum_array_is_rejected() -> None:
    responses.add(
        responses.GET,
        OPEN_METEO_ENDPOINT,
        json={
            "daily": {
                "time": ["2026-09-26", "2026-09-27"],
                "temperature_2m_max": [14.5, 12.1],
                "temperature_2m_min": [6.3],
            }
        },
        status=200,
    )

    with pytest.raises(ValueError, match="temperature_2m_min"):
        OpenMeteoClient(LONDON).get_forecast()

from typing import ClassVar

import pytest
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
    inspect,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from hive_app.data.mysql.client import MariaDBClient

# The weather tables as they were before shortwave radiation, cloud cover,
# sunshine duration and the forecast mean temperature were collected.
_LegacyBase = declarative_base()


class _LegacyWeatherObservation(_LegacyBase):  # type: ignore[misc,valid-type]
    __tablename__ = "weather_observation"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(20))
    location = Column(String(20), nullable=False)
    observed_at = Column(DateTime, nullable=False)
    temp = Column(Float)
    humidity = Column(Float)
    pressure = Column(Float)
    wind_speed = Column(Float)
    precipitation = Column(Float)


class _LegacyWeatherForecast(_LegacyBase):  # type: ignore[misc,valid-type]
    __tablename__ = "weather_forecast"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(String(50), primary_key=True)
    source = Column(String(20))
    target_date = Column(Date, nullable=False)
    max_temp = Column(Float)
    fetched_at = Column(DateTime, nullable=False)


def test_existing_weather_tables_gain_the_solar_and_forecast_mean_columns_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"octopus": "main"})
    _LegacyBase.metadata.create_all(engine)
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

    inspector = inspect(engine)
    observation_columns = {
        c["name"] for c in inspector.get_columns("weather_observation")
    }
    forecast_columns = {c["name"] for c in inspector.get_columns("weather_forecast")}
    assert {"shortwave_radiation", "cloud_cover", "sunshine_duration"} <= (
        observation_columns
    )
    assert "mean_temp" in forecast_columns

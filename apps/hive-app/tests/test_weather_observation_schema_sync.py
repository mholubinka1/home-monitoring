from typing import ClassVar

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from hive_app.data.mysql.client import MariaDBClient

# The weather_observation table as it was before the location key existed:
# no location column and no unique key.
_LegacyBase = declarative_base()


class _LegacyWeatherObservation(_LegacyBase):  # type: ignore[misc,valid-type]
    __tablename__ = "weather_observation"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(20))
    observed_at = Column(DateTime, nullable=False)
    temp = Column(Float)
    humidity = Column(Float)
    pressure = Column(Float)
    wind_speed = Column(Float)
    precipitation = Column(Float)


def _legacy_engine(monkeypatch: pytest.MonkeyPatch) -> Engine:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"octopus": "main"})
    _LegacyBase.metadata.create_all(engine)
    monkeypatch.setattr(
        "common.mariadb.client.create_engine", lambda *args, **kwargs: engine
    )
    return engine


def test_an_existing_weather_observation_table_gains_the_location_and_unique_key_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _legacy_engine(monkeypatch)

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
    columns = {c["name"] for c in inspector.get_columns("weather_observation")}
    unique_keys = [
        index["column_names"]
        for index in inspector.get_indexes("weather_observation")
        if index["unique"]
    ]
    assert "location" in columns
    assert ["source", "location", "observed_at"] in unique_keys


def test_an_existing_observation_row_survives_the_sync_with_an_empty_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _legacy_engine(monkeypatch)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO weather_observation (source, observed_at, temp) "
                "VALUES ('open-meteo', '2026-09-25 12:00:00', 14.5)"
            )
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
            text("SELECT source, location, temp FROM weather_observation")
        ).all()
    assert [tuple(row) for row in rows] == [("open-meteo", "", 14.5)]

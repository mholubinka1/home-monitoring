from datetime import UTC, datetime
from typing import ClassVar

import pytest
from sqlalchemy import Column, DateTime, Float, Integer, String, create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from hive_app.data.mysql.client import MariaDBClient

# The weather_location table as it was before it recorded the postcode the
# location was derived from.
_LegacyBase = declarative_base()


class _LegacyWeatherLocation(_LegacyBase):  # type: ignore[misc,valid-type]
    __tablename__ = "weather_location"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(Integer, primary_key=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    source = Column(String(20), nullable=False)
    resolved_at = Column(DateTime, nullable=False)


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


def test_an_existing_weather_location_table_gains_the_derived_from_postcode_column_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _legacy_engine(monkeypatch)
    with engine.begin() as connection:
        connection.execute(
            _LegacyWeatherLocation.__table__.insert().values(  # type: ignore[attr-defined]
                id=1,
                latitude=51.4,
                longitude=-0.05,
                source="postcode",
                resolved_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        )

    client = MariaDBClient(
        MariaDBSettings(
            host="localhost",
            port=3306,
            database="main",
            username="test",
            password="test",
        )
    )

    columns = {c["name"] for c in inspect(engine).get_columns("weather_location")}
    assert "derived_from_postcode" in columns
    location = client.read_weather_location()
    assert location is not None
    assert (location.latitude, location.source) == (51.4, "postcode")
    assert location.derived_from_postcode is None

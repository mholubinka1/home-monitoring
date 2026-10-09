from typing import ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
)

from common.mariadb.model import SQLBase, job_run

__all__ = ["SQLBase", "job_run"]


class heating_status(SQLBase):
    __tablename__ = "heating_status"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    polled_at = Column(DateTime, nullable=False)
    current_temp = Column(Float)
    target_temp = Column(Float)
    mode = Column(String(20))
    state = Column(String(20))
    boost_active = Column(Boolean)
    boost_ends_at = Column(DateTime)
    schedule = Column(JSON)
    working = Column(Boolean)


class weather_observation(SQLBase):
    __tablename__ = "weather_observation"
    # A unique Index (not a UniqueConstraint): Schema Sync only creates
    # missing indexes on existing tables.
    __table_args__ = (
        Index(
            "uq_weather_observation_hour",
            "source",
            "location",
            "observed_at",
            unique=True,
        ),
        {"schema": "octopus"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(20))
    # The server default is what lets Schema Sync add this NOT NULL column to
    # a table that already has rows (they get '').
    location = Column(String(20), nullable=False, server_default="")
    observed_at = Column(DateTime, nullable=False)
    temp = Column(Float)
    humidity = Column(Float)
    pressure = Column(Float)
    wind_speed = Column(Float)
    precipitation = Column(Float)
    shortwave_radiation = Column(Float)
    cloud_cover = Column(Float)
    sunshine_duration = Column(Float)


class weather_forecast(SQLBase):
    __tablename__ = "weather_forecast"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(String(50), primary_key=True)
    source = Column(String(20))
    target_date = Column(Date, nullable=False)
    max_temp = Column(Float)
    mean_temp = Column(Float)
    fetched_at = Column(DateTime, nullable=False)


class weather_location(SQLBase):
    """One row (fixed id=1): the Weather Location resolved lazily by hive-app
    -- see ADR-0028. Never written for an explicit `location` config."""

    __tablename__ = "weather_location"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(Integer, primary_key=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    source = Column(String(20), nullable=False)
    derived_from_postcode = Column(String(20), nullable=True)
    resolved_at = Column(DateTime, nullable=False)

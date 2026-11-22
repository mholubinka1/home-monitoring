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
    # Indexed for the Hive data watchdog's minute-by-minute newest-row lookup
    # (ADR-0031); Schema Sync adds a missing index to an existing table.
    __table_args__ = (
        Index("ix_heating_status_polled_at", "polled_at"),
        {"schema": "octopus"},
    )

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
    online = Column(Boolean)


class heating_schedule(SQLBase):
    """Hive's weekly schedule, one row each time it changes."""

    __tablename__ = "heating_schedule"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    captured_at = Column(DateTime, nullable=False)
    schedule = Column(JSON, nullable=False)


class presence_period(SQLBase):
    """One stretch of a presence state; ended_at is NULL while it is open."""

    __tablename__ = "presence_period"
    __table_args__ = (
        Index("ix_presence_period_started_at", "started_at"),
        {"schema": "octopus"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    state = Column(String(20), nullable=False)
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    phones_seen = Column(Integer, nullable=False)


# What a heating_control_action row records: the action taken and its outcome.
ACTION_AWAY_OVERRIDE = "away_override"
ACTION_RESTORE_SCHEDULE = "restore_schedule"
ACTION_COMFORT_SCHEDULE = "comfort_schedule"
OUTCOME_APPLIED = "applied"
OUTCOME_DRY_RUN = "dry_run"
OUTCOME_FAILED = "failed"
OUTCOME_NOT_VERIFIED = "not_verified"
OUTCOME_SKIPPED = "skipped"


class heating_control_action(SQLBase):
    """One thing presence control did (or would have done) to the heating."""

    __tablename__ = "heating_control_action"
    __table_args__ = (
        Index("ix_heating_control_action_acted_at", "acted_at"),
        {"schema": "octopus"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    acted_at = Column(DateTime, nullable=False)
    action = Column(String(30), nullable=False)
    mode = Column(String(20), nullable=True)
    target = Column(Float, nullable=True)
    dry_run = Column(Boolean, nullable=False)
    outcome = Column(String(20), nullable=False)
    detail = Column(String(255), nullable=True)


# One observation per hour per source and location: the unique key, which the
# keyed upsert also resolves conflicts on.
WEATHER_OBSERVATION_KEY = ("source", "location", "observed_at")


class weather_observation(SQLBase):
    __tablename__ = "weather_observation"
    # A unique Index (not a UniqueConstraint): Schema Sync only creates
    # missing indexes on existing tables.
    __table_args__ = (
        Index("uq_weather_observation_hour", *WEATHER_OBSERVATION_KEY, unique=True),
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
    min_temp = Column(Float)
    fetched_at = Column(DateTime, nullable=False)


# One forecast per hour per source and location: the unique key, which the
# keyed upsert also resolves conflicts on, so a refresh overwrites the hour.
WEATHER_FORECAST_HOUR_KEY = ("source", "location", "forecast_at")


class weather_forecast_hour(SQLBase):
    __tablename__ = "weather_forecast_hour"
    __table_args__ = (
        Index("uq_weather_forecast_hour", *WEATHER_FORECAST_HOUR_KEY, unique=True),
        {"schema": "octopus"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(20))
    location = Column(String(20), nullable=False)
    forecast_at = Column(DateTime, nullable=False)
    temp = Column(Float)
    shortwave_radiation = Column(Float)
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

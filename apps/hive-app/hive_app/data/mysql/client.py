import logging.config
from datetime import UTC, date, datetime
from logging import Logger, getLogger
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from common.config import MariaDBSettings
from common.mariadb.client import MariaDBClientBase
from common.mariadb.model import job_run
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.model import (
    HeatingStatus,
    ResolvedLocation,
    WeatherForecastDay,
    WeatherForecastHour,
    WeatherObservation,
)
from hive_app.data.mysql import model as sql_model
from hive_app.data.mysql.model import (
    WEATHER_FORECAST_HOUR_KEY,
    WEATHER_OBSERVATION_KEY,
    SQLBase,
)

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

# weather_location holds a single row at this fixed primary key.
_WEATHER_LOCATION_ROW_ID = 1


def _forecast_scoped_id(source: str, target_date: date) -> str:
    return f"{source}_{target_date.strftime('%Y%m%d')}"


def _json_safe(value: Any) -> Any:
    """A copy of `value` with datetimes/dates as ISO-8601 strings, so it fits a
    JSON column. Builds new containers; never mutates the caller's structure."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


class MariaDBClient(MariaDBClientBase):
    def __init__(self, settings: MariaDBSettings) -> None:
        super().__init__(settings, declarative_base=SQLBase, logger=logger)

    def write_heating_status(self, status: HeatingStatus) -> None:
        # sqlalchemy-stubs models every Numeric subclass (Float included) as
        # TypeEngine[Decimal], so it reports a float/Decimal mismatch here even
        # though SQLAlchemy's real runtime Float column stores/returns a plain
        # Python float -- a known stub-accuracy gap, not a real type error.
        record = sql_model.heating_status(
            polled_at=status.polled_at,
            current_temp=status.current_temp,  # type: ignore[misc]
            target_temp=status.target_temp,  # type: ignore[misc]
            mode=status.mode,
            state=status.state,
            boost_active=status.boost_active,
            boost_ends_at=status.boost_ends_at,
            schedule=_json_safe(status.schedule),
            working=status.working,
        )
        self._write_all([record], "Heating status data")

    def write_weekly_schedule(
        self, captured_at: datetime, schedule: dict[str, Any] | None
    ) -> None:
        """Stores the weekly schedule when it differs from the newest stored
        one; does nothing for None."""
        if schedule is None:
            return
        with self.session_read_scope() as session:
            newest = (
                session.query(sql_model.heating_schedule.schedule)
                .order_by(sql_model.heating_schedule.id.desc())
                .limit(1)
                .scalar()
            )
        if newest == schedule:
            return
        record = sql_model.heating_schedule(captured_at=captured_at, schedule=schedule)
        self._write_all([record], "Heating schedule data")

    def latest_heating_polled_at(self) -> datetime | None:
        """When the newest heating_status row was polled (UTC), or None."""
        with self.session_read_scope() as session:
            latest = session.query(
                func.max(sql_model.heating_status.polled_at)
            ).scalar()
            # The database stores naive UTC; stamp it back as UTC.
            return latest.replace(tzinfo=UTC) if latest is not None else None

    def first_heating_polled_after(self, moment: datetime) -> datetime | None:
        """When the first heating_status row after `moment` was polled (UTC)."""
        with self.session_read_scope() as session:
            first = (
                session.query(func.min(sql_model.heating_status.polled_at))
                .filter(
                    sql_model.heating_status.polled_at > moment.replace(tzinfo=None)
                )
                .scalar()
            )
            return first.replace(tzinfo=UTC) if first is not None else None

    def latest_failed_job_error(self, job_name: str, since: datetime) -> str | None:
        """The first non-blank line (at most 200 characters) of the error of the most
        recent failed run of the job after `since`, or None if there is none.
        One line only: a database error's later lines carry SQL and values."""
        with self.session_read_scope() as session:
            error = (
                session.query(job_run.error_message)
                .filter(
                    job_run.job_name == job_name,
                    job_run.status == "failure",
                    job_run.ran_at > since.replace(tzinfo=None),
                )
                .order_by(job_run.ran_at.desc(), job_run.id.desc())
                .limit(1)
                .scalar()
            )
            first_line = next(
                (line.strip() for line in (error or "").splitlines() if line.strip()),
                None,
            )
            return first_line[:200] if first_line else None

    def write_weather_observations(
        self, observations: list[WeatherObservation]
    ) -> None:
        # sqlalchemy-stubs models every Numeric subclass (Float included) as
        # TypeEngine[Decimal], so it reports a float/Decimal mismatch here even
        # though SQLAlchemy's real runtime Float column stores/returns a plain
        # Python float -- a known stub-accuracy gap, not a real type error.
        records = [
            sql_model.weather_observation(
                source=observation.source,
                location=observation.location,
                observed_at=observation.observed_at,
                temp=observation.temp,  # type: ignore[misc]
                humidity=observation.humidity,  # type: ignore[misc]
                pressure=observation.pressure,  # type: ignore[misc]
                wind_speed=observation.wind_speed,  # type: ignore[misc]
                precipitation=observation.precipitation,  # type: ignore[misc]
                shortwave_radiation=observation.shortwave_radiation,  # type: ignore[misc]
                cloud_cover=observation.cloud_cover,  # type: ignore[misc]
                sunshine_duration=observation.sunshine_duration,  # type: ignore[misc]
            )
            for observation in observations
        ]
        # Redacted: a SQLAlchemy error embeds the bound values, including the
        # location key (the Weather Location's rounded coordinates).
        self._write_all(
            records,
            "Weather observation data",
            key_columns=WEATHER_OBSERVATION_KEY,
            redact_errors=True,
        )

    def write_weather_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        # sqlalchemy-stubs models every Numeric subclass (Float included) as
        # TypeEngine[Decimal], so it reports a float/Decimal mismatch here even
        # though SQLAlchemy's real runtime Float column stores/returns a plain
        # Python float -- a known stub-accuracy gap, not a real type error.
        records = [
            sql_model.weather_forecast(
                id=_forecast_scoped_id(day.source, day.target_date),
                source=day.source,
                target_date=day.target_date,
                max_temp=day.max_temp,  # type: ignore[misc]
                mean_temp=day.mean_temp,  # type: ignore[misc]
                min_temp=day.min_temp,  # type: ignore[misc]
                fetched_at=day.fetched_at,
            )
            for day in forecast
        ]
        self._write_all(records, "Weather forecast data")

    def write_weather_forecast_hours(self, hours: list[WeatherForecastHour]) -> None:
        # See write_weather_observations for why the float assignments need an
        # ignore, and why errors are redacted.
        records = [
            sql_model.weather_forecast_hour(
                source=hour.source,
                location=hour.location,
                forecast_at=hour.forecast_at,
                temp=hour.temp,  # type: ignore[misc]
                shortwave_radiation=hour.shortwave_radiation,  # type: ignore[misc]
                fetched_at=hour.fetched_at,
            )
            for hour in hours
        ]
        self._write_all(
            records,
            "Weather forecast hour data",
            key_columns=WEATHER_FORECAST_HOUR_KEY,
            redact_errors=True,
        )

    def read_weather_observation_hours(
        self, location: str, start: datetime, end: datetime
    ) -> list[tuple[str, datetime]]:
        """(source, observed_at in UTC) of every stored hour for the location
        key with start <= observed_at < end, whatever its source."""
        with self.session_read_scope() as session:
            rows = (
                session.query(
                    sql_model.weather_observation.source,
                    sql_model.weather_observation.observed_at,
                )
                .filter(
                    sql_model.weather_observation.location == location,
                    sql_model.weather_observation.observed_at >= start,
                    sql_model.weather_observation.observed_at < end,
                )
                .all()
            )
            # The database stores naive UTC; stamp it back as UTC.
            return [(source, observed.replace(tzinfo=UTC)) for source, observed in rows]

    def oldest_weather_observation(self, location: str) -> datetime | None:
        """The earliest stored hour (UTC) for the location key, whatever its
        source, or None if there is none."""
        with self.session_read_scope() as session:
            oldest = (
                session.query(func.min(sql_model.weather_observation.observed_at))
                .filter(sql_model.weather_observation.location == location)
                .scalar()
            )
            # The database stores naive UTC; stamp it back as UTC.
            return oldest.replace(tzinfo=UTC) if oldest is not None else None

    @staticmethod
    def _weather_location_row(session: Session) -> sql_model.weather_location | None:
        return (
            session.query(sql_model.weather_location)
            .filter_by(id=_WEATHER_LOCATION_ROW_ID)
            .first()
        )

    def write_weather_location(self, location: ResolvedLocation) -> None:
        # See write_weather_observations for why the float assignments need an
        # ignore.
        record = sql_model.weather_location(
            id=_WEATHER_LOCATION_ROW_ID,
            latitude=location.latitude,  # type: ignore[misc]
            longitude=location.longitude,  # type: ignore[misc]
            source=location.source,
            derived_from_postcode=location.derived_from_postcode,
            resolved_at=datetime.now(UTC),
        )
        # The row records the Account Postcode it was derived from, which a
        # SQLAlchemy error message would embed -- so errors are redacted.
        self._write_all([record], "Weather location", redact_errors=True)

    def read_weather_location(self) -> ResolvedLocation | None:
        with self.session_read_scope() as session:
            row = self._weather_location_row(session)
            if row is None:
                return None
            return ResolvedLocation(
                latitude=float(row.latitude),
                longitude=float(row.longitude),
                source=row.source,  # type: ignore[arg-type]
                # Same stub gap as above: a nullable String column reads as Column.
                derived_from_postcode=row.derived_from_postcode,  # type: ignore[arg-type]
            )

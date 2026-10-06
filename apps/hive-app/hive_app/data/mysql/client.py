import logging.config
from datetime import UTC, date, datetime
from logging import Logger, getLogger
from typing import Any

from common.config import MariaDBSettings
from common.mariadb.client import MariaDBClientBase
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.model import (
    HeatingStatus,
    ResolvedLocation,
    WeatherForecastDay,
    WeatherObservation,
)
from hive_app.data.mysql import model as sql_model
from hive_app.data.mysql.model import SQLBase

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
        )
        self._write_all([record], "Heating status data")

    def write_weather_observation(self, observation: WeatherObservation) -> None:
        # sqlalchemy-stubs models every Numeric subclass (Float included) as
        # TypeEngine[Decimal], so it reports a float/Decimal mismatch here even
        # though SQLAlchemy's real runtime Float column stores/returns a plain
        # Python float -- a known stub-accuracy gap, not a real type error.
        record = sql_model.weather_observation(
            source=observation.source,
            observed_at=observation.observed_at,
            temp=observation.temp,  # type: ignore[misc]
            humidity=observation.humidity,  # type: ignore[misc]
            pressure=observation.pressure,  # type: ignore[misc]
            wind_speed=observation.wind_speed,  # type: ignore[misc]
            precipitation=observation.precipitation,  # type: ignore[misc]
        )
        self._write_all([record], "Weather observation data")

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
                fetched_at=day.fetched_at,
            )
            for day in forecast
        ]
        self._write_all(records, "Weather forecast data")

    def write_weather_location(self, location: ResolvedLocation) -> None:
        # See write_weather_observation for why the float assignments need an
        # ignore.
        record = sql_model.weather_location(
            id=_WEATHER_LOCATION_ROW_ID,
            latitude=location.latitude,  # type: ignore[misc]
            longitude=location.longitude,  # type: ignore[misc]
            source=location.source,
            resolved_at=datetime.now(UTC),
        )
        self._write_all([record], "Weather location")

    def read_weather_location(self) -> ResolvedLocation | None:
        with self.session_read_scope() as session:
            row = (
                session.query(sql_model.weather_location)
                .filter_by(id=_WEATHER_LOCATION_ROW_ID)
                .first()
            )
            if row is None:
                return None
            return ResolvedLocation(
                latitude=float(row.latitude),
                longitude=float(row.longitude),
                source=row.source,
            )

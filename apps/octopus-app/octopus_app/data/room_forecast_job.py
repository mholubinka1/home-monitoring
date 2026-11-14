"""Runs the thermal model forward from the room's newest reading to the end of
tomorrow and stores the hourly forecast (ADR-0035)."""

import logging.config
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from logging import Logger, getLogger

from common import local_day
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import heating_hours, room_forecast
from octopus_app.data.model import (
    HourlyWeather,
    RoomForecastRecord,
    ThermalModelRecord,
)
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.thermal import (
    MINUTES_PER_HOUR,
    ONE_HOUR,
    WATTS_PER_KILOWATT,
    ThermalNumbers,
)

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

LIVE = "live"
CURRENT = "current"
# A target no room is below: the heating fraction clamps to zero.
NO_TARGET = -math.inf
OFF_MODE = "OFF"
MAX_READING_AGE = timedelta(minutes=30)


class RoomForecastJob:
    def __init__(self, mariadb: MariaDBClient, region_code: str) -> None:
        self._mariadb = mariadb
        self._region_code = region_code

    def refresh(self, now: datetime | None = None) -> None:
        """Replaces the live current-schedule forecast: hourly, from the hour
        containing now to the end of tomorrow's local day."""
        now = (now or datetime.now(UTC)).astimezone(UTC)
        first = now.replace(minute=0, second=0, microsecond=0, tzinfo=None)
        end = (
            local_day.start_of_local_day(
                local_day.to_local_date(now) + timedelta(days=2)
            )
            .astimezone(UTC)
            .replace(tzinfo=None)
        )
        stored = self._mariadb.read_latest_thermal_model()
        status = self._mariadb.read_latest_heating_status()
        numbers = None if stored is None else _numbers_of(stored.model)
        if numbers is None or status is None or status[1] is None:
            return
        if now.replace(tzinfo=None) - status[0] > MAX_READING_AGE:
            logger.info("Room forecast: the newest reading is stale, nothing stored.")
            return
        weather = self._mariadb.read_hourly_weather_forecast(first, end)
        schedule = room_forecast.parse_schedule(
            self._mariadb.read_latest_heating_schedule()
        )
        if schedule is None:
            # Not read as "no heating": a wrong line is worse than none.
            logger.warning(
                "Room forecast: the weekly schedule is missing or unreadable, "
                "no current-schedule forecast stored."
            )
            return
        heating_off = status[2] == OFF_MODE  # the thermostat will not heat
        rows = self._current_rows(
            numbers,
            status[1],
            lambda hour: (
                NO_TARGET
                if heating_off
                else room_forecast.target_at(schedule, _local(hour))
            ),
            weather,
            first,
            end,
            now,
        )
        if not rows:
            # No weather for the first hour: keep the earlier rows, not replace
            # them with none.
            logger.info("Room forecast: no weather for the first hour, nothing stored.")
            return
        self._mariadb.replace_room_forecast(LIVE, CURRENT, rows)
        logger.info(f"Room forecast: stored {len(rows)} current hour(s).")

    def _current_rows(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        self,
        numbers: ThermalNumbers,
        room: float,
        target_of: Callable[[datetime], float],
        weather: dict[datetime, HourlyWeather],
        first: datetime,
        end: datetime,
        now: datetime,
    ) -> list[RoomForecastRecord]:
        rows = []
        boiler_kw = self._boiler_kw(now)
        previous_fraction = self._previous_fraction(first)
        hour = first
        while hour < end:
            here, after = weather.get(hour), weather.get(hour + ONE_HOUR)
            if here is None or after is None:
                break
            fraction, end_room = room_forecast.hold_target(
                numbers,
                room,
                target_of(hour),
                here.temp,
                (after.radiation or 0.0) / WATTS_PER_KILOWATT,
                previous_fraction,
            )
            rows.append(
                self._row(
                    hour, room, round(fraction * MINUTES_PER_HOUR), boiler_kw, now
                )
            )
            room, previous_fraction = end_room, fraction
            hour += ONE_HOUR
        return rows

    def _row(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        hour: datetime,
        room: float,
        on_minutes: int,
        boiler_kw: float,
        now: datetime,
    ) -> RoomForecastRecord:
        kwh, cost = heating_hours.estimated_cost(
            on_minutes,
            self._mariadb.read_gas_unit_rate(self._region_code, hour),
            boiler_kw,
        )
        return RoomForecastRecord(
            kind=LIVE,
            scenario=CURRENT,
            hour_start=hour,
            room_temp=room,
            on_minutes=on_minutes,
            heating_kwh=kwh,
            cost_gbp=cost,
            computed_at=now.replace(tzinfo=None),
        )

    def _boiler_kw(self, now: datetime) -> float:
        """The boiler kW learned from the measured hours of the 28 days before now."""
        end = now.replace(minute=0, second=0, microsecond=0, tzinfo=None)
        earlier = self._mariadb.read_heating_hours(
            end - timedelta(days=heating_hours.BASELINE_DAYS), end
        )
        return heating_hours.learned_boiler_kw(
            (row.on_minutes, row.heating_kwh or 0.0)
            for row in earlier
            if row.source == heating_hours.MEASURED
        )

    def _previous_fraction(self, first: datetime) -> float:
        """The heating of the hour before the first, as a fraction; none stored
        counts as none."""
        stored = self._mariadb.read_heating_hours(first - ONE_HOUR, first)
        return stored[0].on_minutes / MINUTES_PER_HOUR if stored else 0.0


def _local(hour: datetime) -> datetime:
    """A naive UTC hour as naive local wall-clock time."""
    return (
        hour.replace(tzinfo=UTC)
        .astimezone(local_day.get_local_timezone())
        .replace(tzinfo=None)
    )


def _numbers_of(record: ThermalModelRecord) -> ThermalNumbers | None:
    """The model's numbers, or None when it is unusable."""
    numbers = [
        record.loss_per_degree,
        record.heating_gain,
        record.carried_gain,
        record.sun_gain,
        record.constant_gain,
    ]
    if not record.usable or any(number is None for number in numbers):
        return None
    return ThermalNumbers(*numbers)  # type: ignore[arg-type]

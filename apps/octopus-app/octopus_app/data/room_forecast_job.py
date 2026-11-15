"""Runs the thermal model forward from the room's newest reading to the end of
tomorrow and stores the hourly forecast (ADR-0035)."""

import logging.config
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from logging import Logger, getLogger

from common import local_day
from octopus_app.common.config import ComfortWindows
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import heating_hours, room_forecast
from octopus_app.data.model import (
    HourlyWeather,
    RoomForecastRecord,
    RoomForecastSlotRecord,
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
DAY_AHEAD = "day_ahead"
ONE_DAY = timedelta(days=1)
CURRENT = "current"
PLAN = "plan"
OFF_MODE = "OFF"
MAX_READING_AGE = timedelta(minutes=30)


@dataclass(frozen=True)
class _Inputs:
    """What both forecasts start from."""

    numbers: ThermalNumbers
    start: room_forecast.Start
    hours: list[datetime]  # naive UTC starts, to the first weather gap
    weather: list[room_forecast.ForecastHour]  # the same hours, local starts
    boiler_kw: float
    now: datetime
    tomorrow: tuple[datetime, datetime]  # [start, end) of its local day, naive UTC


class RoomForecastJob:
    def __init__(
        self,
        mariadb: MariaDBClient,
        region_code: str,
        comfort_windows: ComfortWindows | None = None,
    ) -> None:
        self._mariadb = mariadb
        self._region_code = region_code
        self._windows = comfort_windows or ComfortWindows()

    def refresh(self, now: datetime | None = None) -> None:
        """Replaces the live forecasts, current schedule and comfort plan, each on
        its own inputs: hourly, from the hour containing the newest reading, and
        from the reading's time on, to the end of tomorrow's local day."""
        now = (now or datetime.now(UTC)).astimezone(UTC)
        end = _naive_utc_start_of(local_day.to_local_date(now) + 2 * ONE_DAY)
        stored = self._mariadb.read_latest_thermal_model()
        status = self._mariadb.read_latest_heating_status()
        numbers = None if stored is None else _numbers_of(stored.model)
        if numbers is None:
            logger.info("Room forecast: no usable thermal model, nothing stored.")
            return
        if status is None or status[1] is None:
            logger.info("Room forecast: no thermostat reading, nothing stored.")
            return
        if now.replace(tzinfo=None) - status[0] > MAX_READING_AGE:
            logger.info("Room forecast: the newest reading is stale, nothing stored.")
            return
        first = status[0].replace(minute=0, second=0, microsecond=0)
        weather = self._mariadb.read_hourly_weather_forecast(first, end)
        hours = _hours_with_weather(weather, first, end)
        if not hours:
            # Keep the earlier rows, not replace them with none.
            logger.info("Room forecast: no weather for the first hour, nothing stored.")
            return
        inputs = _Inputs(
            numbers,
            room_forecast.Start(
                status[1],
                self._previous_fraction(first),
                _local(status[0]),
                self._seen_fraction(first, status[0]),
            ),
            hours,
            [
                room_forecast.ForecastHour(
                    _local(hour),
                    weather[hour].temp,
                    (weather[hour + ONE_HOUR].radiation or 0.0) / WATTS_PER_KILOWATT,
                )
                for hour in hours
            ],
            self._boiler_kw(now),
            now,
            (_naive_utc_start_of(local_day.to_local_date(now) + ONE_DAY), end),
        )
        self._store_current(inputs, status[2] == OFF_MODE)
        self._store_plan(inputs, local_day.to_local_date(now))

    def _store_current(self, inputs: _Inputs, heating_off: bool) -> None:
        schedule = room_forecast.parse_schedule(
            self._mariadb.read_latest_heating_schedule()
        )
        if schedule is None:
            # Not read as "no heating": a wrong line is worse than none.
            logger.warning(
                "Room forecast: the weekly schedule is missing or unreadable, "
                "no current-schedule forecast stored."
            )
            # Nor is the earlier line left drawn as today's.
            self._mariadb.replace_room_forecast(LIVE, CURRENT, [])
            self._mariadb.replace_room_forecast_slots(CURRENT, [])
            return
        self._store_slots(schedule, local_day.to_local_date(inputs.now), inputs.now)
        planned = room_forecast.hold_forecast(
            inputs.numbers,
            inputs.start,
            inputs.weather,
            lambda local: (
                # the thermostat will not heat
                [room_forecast.Part(1.0, room_forecast.NO_TARGET)]
                if heating_off
                else room_forecast.schedule_parts(schedule, local)
            ),
        )
        self._store(CURRENT, inputs, planned)

    def _store_slots(
        self, schedule: room_forecast.Schedule, today: date, now: datetime
    ) -> None:
        """Today's and tomorrow's current slots, whatever the thermostat's mode."""
        naive_now = now.replace(tzinfo=None)
        rows = [
            RoomForecastSlotRecord(
                day,
                CURRENT,
                position,
                _utc_of(slot.start),
                _utc_of(slot.end),
                slot.target,
                naive_now,
            )
            for day in (today, today + ONE_DAY)
            for position, slot in enumerate(room_forecast.schedule_slots(schedule, day))
        ]
        self._mariadb.replace_room_forecast_slots(CURRENT, rows)

    def _store_plan(self, inputs: _Inputs, today: date) -> None:
        # The newest comfort temperature up to today's; tomorrow has no row yet, so
        # it serves both days.
        comfort = self._mariadb.read_latest_comfort_temperature(
            before=today + timedelta(days=1)
        )
        if comfort is None:
            logger.info("Room forecast: no comfort temperature, no plan stored.")
            self._mariadb.replace_room_forecast(LIVE, PLAN, [])
            self._mariadb.replace_room_forecast_slots(PLAN, [])
            return
        self._store_plan_slots(inputs, room_forecast.round_up_to_half(comfort))
        planned = room_forecast.comfort_plan(
            inputs.numbers,
            inputs.start,
            inputs.weather,
            room_forecast.round_up_to_half(comfort),
            self._windows,
        )
        self._store(PLAN, inputs, planned)

    def _store_plan_slots(self, inputs: _Inputs, target: float) -> None:
        """The plan's slot of each local day, its start kept once it has passed."""
        naive_now = inputs.now.replace(tzinfo=None)
        rows = []
        # Each slot belongs to the day its window opened on.
        for day, slot in room_forecast.plan_slots(
            inputs.numbers, inputs.start, inputs.weather, target, self._windows
        ):
            starts_at = _utc_of(slot.start)
            for stored in self._mariadb.read_room_forecast_slots(PLAN, day):
                if stored.starts_at <= naive_now:
                    starts_at = stored.starts_at
            rows.append(
                RoomForecastSlotRecord(
                    day,
                    PLAN,
                    0,
                    starts_at,
                    _utc_of(slot.end),
                    slot.target,
                    naive_now,
                )
            )
        self._mariadb.replace_room_forecast_slots(PLAN, rows)

    def _store(
        self,
        scenario: str,
        inputs: _Inputs,
        planned: list[room_forecast.PlannedHour],
    ) -> None:
        rows = [
            self._row(scenario, hour, step, inputs)
            for hour, step in zip(inputs.hours, planned)
        ]
        self._mariadb.replace_room_forecast(LIVE, scenario, rows)
        logger.info(f"Room forecast: stored {len(rows)} {scenario} hour(s).")
        # Tomorrow's hours are also kept as the day-ahead forecast: rewritten by
        # each run until midnight, then no run touches them again. A run cut short
        # by a weather gap leaves the earlier rows, so a frozen day is never
        # shortened.
        tomorrow_start, tomorrow_end = inputs.tomorrow
        tomorrow = [
            replace(row, kind=DAY_AHEAD)
            for row in rows
            if tomorrow_start <= row.hour_start < tomorrow_end
        ]
        if len(tomorrow) < (tomorrow_end - tomorrow_start) // ONE_HOUR:
            logger.info(
                f"Room forecast: {scenario} does not cover all of tomorrow, "
                "day-ahead rows left as they were."
            )
            return
        self._mariadb.replace_room_forecast(
            DAY_AHEAD, scenario, tomorrow, tomorrow_start, tomorrow_end
        )

    def _row(
        self,
        scenario: str,
        hour: datetime,
        step: room_forecast.PlannedHour,
        inputs: _Inputs,
    ) -> RoomForecastRecord:
        on_minutes = round(step.fraction * MINUTES_PER_HOUR)
        kwh, cost = heating_hours.estimated_cost(
            on_minutes,
            self._mariadb.read_gas_unit_rate(self._region_code, hour),
            inputs.boiler_kw,
        )
        return RoomForecastRecord(
            kind=LIVE,
            scenario=scenario,
            hour_start=hour,
            room_temp=step.room,
            on_minutes=on_minutes,
            heating_kwh=kwh,
            cost_gbp=cost,
            computed_at=inputs.now.replace(tzinfo=None),
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

    def _seen_fraction(self, first: datetime, reading: datetime) -> float:
        """The heating already seen in the first hour, as a fraction: the minutes
        its polls before the reading were on, at most the minutes gone by."""
        on = heating_hours.on_poll_minutes(
            self._mariadb.read_heating_polls(first, reading)
        )
        return min(on, (reading - first) / timedelta(minutes=1)) / MINUTES_PER_HOUR


def _hours_with_weather(
    weather: dict[datetime, HourlyWeather], first: datetime, end: datetime
) -> list[datetime]:
    """The hours from first to end with weather, up to the first gap. An hour
    needs its own stamp for the temperature and the next one for the radiation."""
    hours = []
    hour = first
    while hour < end and hour in weather and hour + ONE_HOUR in weather:
        hours.append(hour)
        hour += ONE_HOUR
    return hours


def _naive_utc_start_of(day: date) -> datetime:
    """Midnight starting the local day, as naive UTC."""
    return local_day.start_of_local_day(day).astimezone(UTC).replace(tzinfo=None)


def _utc_of(local: datetime) -> datetime:
    """A naive local wall-clock time as naive UTC."""
    return (
        local.replace(tzinfo=local_day.get_local_timezone())
        .astimezone(UTC)
        .replace(tzinfo=None)
    )


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

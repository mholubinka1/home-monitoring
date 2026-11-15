"""Runs the thermal model forward from the room's newest reading to the end of
tomorrow in 2-minute steps and stores the forecast every 10 minutes (ADR-0035,
ADR-0036)."""

import logging.config
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from logging import Logger, getLogger

from common import local_day
from octopus_app.common.config import ComfortWindows
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import heating_intervals, room_forecast
from octopus_app.data.model import (
    HourlyWeather,
    RoomForecastIntervalRecord,
    RoomForecastSlotRecord,
    ThermalModelRecord,
)
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.room_forecast import STEP, STEP_MINUTES
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
STORED_MINUTES = 10  # one row per this many minutes
STORED_SPAN = timedelta(minutes=STORED_MINUTES)


@dataclass(frozen=True)
class _Inputs:  # pylint: disable=too-many-instance-attributes
    """What both forecasts start from."""

    model: room_forecast.RoomModel
    start: room_forecast.Start
    hours: list[datetime]  # naive UTC starts, to the first weather gap
    weather: list[room_forecast.ForecastHour]  # the same hours, local starts
    boiler_kw: float
    now: datetime
    tomorrow: tuple[datetime, datetime]  # [start, end) of its local day, naive UTC
    # Gas unit rates by hour start, read once per distinct hour of a run.
    unit_rates: dict[datetime, float | None] = field(default_factory=dict)


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
        its own inputs: in 2-minute steps from the step containing the newest
        reading, to the end of tomorrow's local day, stored every 10 minutes."""
        now = (now or datetime.now(UTC)).astimezone(UTC)
        end = _naive_utc_start_of(local_day.to_local_date(now) + 2 * ONE_DAY)
        stored = self._mariadb.read_latest_thermal_model()
        status = self._mariadb.read_latest_heating_status()
        model = None if stored is None else _room_model_of(stored.model)
        if model is None:
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
            model,
            room_forecast.Start(
                status[1], _local(status[0]), self._recorded_demand(model, status[0])
            ),
            hours,
            [
                room_forecast.ForecastHour(
                    _local(hour),
                    weather[hour].temp,
                    weather[hour + ONE_HOUR].temp,
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
            inputs.model,
            inputs.start,
            inputs.weather,
            lambda local: (
                # the thermostat will not heat
                room_forecast.NO_TARGET
                if heating_off
                else room_forecast.schedule_target_at_step(schedule, local)
            ),
        )
        self._store(CURRENT, inputs, planned)

    def _store_slots(
        self, schedule: room_forecast.Schedule, today: date, now: datetime
    ) -> None:
        """Today's and tomorrow's current slots, whatever the thermostat's mode."""
        naive_now = now.replace(tzinfo=None)
        rows: list[RoomForecastSlotRecord] = []
        for day in (today, today + ONE_DAY):
            # A slot inside a clock change's skipped hour can have no length: dropped.
            instants = [
                (slot, *_instants_of(slot))
                for slot in room_forecast.schedule_slots(schedule, day)
            ]
            kept = [item for item in instants if item[2] > item[1]]
            # and the slot before it ends where the next begins, not after it.
            rows.extend(
                RoomForecastSlotRecord(
                    day,
                    CURRENT,
                    position,
                    starts,
                    (
                        min(ends, kept[position + 1][1])
                        if position + 1 < len(kept)
                        else ends
                    ),
                    slot.target,
                    naive_now,
                )
                for position, (slot, starts, ends) in enumerate(kept)
            )
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
        target = room_forecast.round_up_to_half(comfort)
        self._store_plan_slots(inputs, target)
        planned = room_forecast.comfort_plan(
            inputs.model, inputs.start, inputs.weather, target, self._windows
        )
        self._store(PLAN, inputs, planned)

    def _store_plan_slots(self, inputs: _Inputs, target: float) -> None:
        """The plan's slot of each local day, its start kept once it has passed."""
        naive_now = inputs.now.replace(tzinfo=None)
        rows: list[RoomForecastSlotRecord] = []
        # Each slot belongs to the day its window opened on.
        for day, slot in room_forecast.plan_slots(
            inputs.model, inputs.start, inputs.weather, target, self._windows
        ):
            starts_at, ends_at = _instants_of(slot)
            if ends_at <= starts_at:
                continue
            # One plan slot a day, so at most one is stored.
            stored = next(iter(self._mariadb.read_room_forecast_slots(PLAN, day)), None)
            if stored is not None and stored.starts_at <= naive_now:
                starts_at = stored.starts_at
            rows.append(
                RoomForecastSlotRecord(
                    day,
                    PLAN,
                    0,
                    starts_at,
                    ends_at,
                    slot.target,
                    naive_now,
                )
            )
        # A day's window that has closed is no longer held, so it has no new slot:
        # the stored one stays until the local day rolls over.
        today = local_day.to_local_date(inputs.now)
        computed = {row.day for row in rows}
        for day in (today, today + ONE_DAY):
            if day not in computed:
                rows.extend(self._mariadb.read_room_forecast_slots(PLAN, day)[:1])
        self._mariadb.replace_room_forecast_slots(PLAN, rows)

    def _store(
        self,
        scenario: str,
        inputs: _Inputs,
        planned: list[room_forecast.PlannedStep],
    ) -> None:
        rows = self._interval_rows(scenario, inputs, planned)
        self._mariadb.replace_room_forecast(LIVE, scenario, rows)
        logger.info(f"Room forecast: stored {len(rows)} {scenario} row(s).")
        # Tomorrow's rows are also kept as the day-ahead forecast: rewritten by
        # each run until midnight, then no run touches them again. A run that
        # does not cover every minute of tomorrow (a weather gap) leaves the
        # earlier rows, so a frozen day is never shortened.
        tomorrow_start, tomorrow_end = inputs.tomorrow
        tomorrow = [
            replace(row, kind=DAY_AHEAD)
            for row in rows
            if tomorrow_start <= row.starts_at < tomorrow_end
        ]
        covered = sum((row.ends_at - row.starts_at for row in tomorrow), timedelta())
        if covered < tomorrow_end - tomorrow_start:
            logger.info(
                f"Room forecast: {scenario} does not cover all of tomorrow, "
                "day-ahead rows left as they were."
            )
            return
        self._mariadb.replace_room_forecast(
            DAY_AHEAD, scenario, tomorrow, tomorrow_start, tomorrow_end
        )

    def _interval_rows(
        self,
        scenario: str,
        inputs: _Inputs,
        planned: list[room_forecast.PlannedStep],
    ) -> list[RoomForecastIntervalRecord]:
        """One row per 10 minutes: the room at its start (the first row holds the
        reading), the minutes demanded over its steps, and the gas they cost at
        the rate in force at its start."""
        by_start: dict[datetime, list[room_forecast.PlannedStep]] = {}
        for step in planned:
            at = inputs.hours[0] + timedelta(minutes=step.offset_minutes)
            by_start.setdefault(_stored_start_of(at), []).append(step)
        rows: list[RoomForecastIntervalRecord] = []
        for starts_at, steps in by_start.items():
            on_minutes = round(sum(step.demand_minutes for step in steps), 1)
            kwh, cost = heating_intervals.estimated_cost(
                on_minutes, self._unit_rate(inputs, starts_at), inputs.boiler_kw
            )
            rows.append(
                RoomForecastIntervalRecord(
                    kind=LIVE,
                    scenario=scenario,
                    starts_at=starts_at,
                    ends_at=starts_at + STORED_SPAN,
                    room_temp=inputs.start.room if not rows else steps[0].room,
                    on_minutes=on_minutes,
                    heating_kwh=kwh,
                    cost_gbp=cost,
                    computed_at=inputs.now.replace(tzinfo=None),
                )
            )
        return rows

    def _unit_rate(self, inputs: _Inputs, starts_at: datetime) -> float | None:
        """The gas unit rate in force at the hour of `starts_at`, read once per
        hour of the run."""
        hour = starts_at.replace(minute=0)
        if hour not in inputs.unit_rates:
            inputs.unit_rates[hour] = self._mariadb.read_gas_unit_rate(
                self._region_code, hour
            )
        return inputs.unit_rates[hour]

    def _boiler_kw(self, now: datetime) -> float:
        """The boiler kW learned from the measured half-hours of the 28 days before
        now."""
        end = heating_intervals.interval_start_of(now.replace(tzinfo=None))
        earlier = self._mariadb.read_heating_intervals(
            end - timedelta(days=heating_intervals.BASELINE_DAYS), end
        )
        return heating_intervals.learned_boiler_kw(
            (row.on_minutes, row.heating_kwh or 0.0)
            for row in earlier
            if row.source == heating_intervals.MEASURED
        )

    def _recorded_demand(
        self, model: room_forecast.RoomModel, reading: datetime
    ) -> list[float]:
        """The minutes the thermostat demanded in each 2-minute step before the
        step containing the reading, over the delay plus the model's step, oldest
        first: the heat that has not finished arriving by the reading."""
        first_step = reading.replace(
            minute=reading.minute // STEP_MINUTES * STEP_MINUTES,
            second=0,
            microsecond=0,
        )
        count = (model.delay_minutes + model.step_minutes) // STEP_MINUTES
        begin = first_step - count * STEP
        on_steps = {
            (polled_at - begin) // STEP
            for polled_at, state, boost in self._mariadb.read_heating_polls(
                begin, first_step
            )
            if heating_intervals.poll_is_on(state, boost)
        }
        return [
            float(STEP_MINUTES) if index in on_steps else 0.0 for index in range(count)
        ]


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


def _stored_start_of(moment: datetime) -> datetime:
    """The start of the 10-minute row holding a moment (naive UTC)."""
    return moment.replace(
        minute=moment.minute // STORED_MINUTES * STORED_MINUTES,
        second=0,
        microsecond=0,
    )


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


def _instants_of(slot: room_forecast.Slot) -> tuple[datetime, datetime]:
    """A slot's naive local start and end as naive UTC, the end never before the
    start (a boundary inside a clock change's skipped hour maps to an earlier
    instant)."""
    starts = _utc_of(slot.start)
    return starts, max(starts, _utc_of(slot.end))


def _local(moment: datetime) -> datetime:
    """A naive UTC moment as naive local wall-clock time."""
    return (
        moment.replace(tzinfo=UTC)
        .astimezone(local_day.get_local_timezone())
        .replace(tzinfo=None)
    )


def _room_model_of(record: ThermalModelRecord) -> room_forecast.RoomModel | None:
    """The model as the forecast uses it, or None when it is unusable."""
    numbers = [
        record.loss_per_degree,
        record.heating_gain,
        record.carried_gain,
        record.sun_gain,
        record.constant_gain,
    ]
    if not record.usable or any(number is None for number in numbers):
        return None
    return room_forecast.RoomModel(
        ThermalNumbers(*numbers),  # type: ignore[arg-type]
        step_minutes=(
            MINUTES_PER_HOUR if record.step_minutes is None else record.step_minutes
        ),
        delay_minutes=(
            0 if record.warm_up_delay_minutes is None else record.warm_up_delay_minutes
        ),
    )

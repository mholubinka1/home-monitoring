"""The room forecast's maths: Hive's weekly schedule read defensively, and one
hour of the thermal model stepped forward while holding a target (ADR-0035).
No I/O."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from octopus_app.common.config import ComfortWindows
from octopus_app.data.thermal import MINUTES_PER_HOUR, ONE_HOUR, ThermalNumbers

SATURDAY = 5
# Halvings of the pre-heat search: far below a minute.
BISECTIONS = 40
ONE_DAY = timedelta(days=1)
# A target no room is below: the heating fraction clamps to zero.
NO_TARGET = -math.inf

DAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

# Per day name, the (start minutes after local midnight, target C) slots, by start.
Schedule = dict[str, list[tuple[int, float]]]


def _is_number(value: object) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _slot(raw: object) -> tuple[int, float] | None:
    if not isinstance(raw, dict):
        return None
    start, value = raw.get("start"), raw.get("value")
    if isinstance(start, bool) or not isinstance(start, int):
        return None
    if not isinstance(value, dict) or not _is_number(value.get("target")):
        return None
    return start, float(value["target"])


def parse_schedule(raw: Any) -> Schedule | None:
    """The schedule as slots by day name, or None when it cannot be read as all
    seven day names, each with slots of integer `start` minutes and a numeric
    `value.target`. Hive's payload has been seen to change, so nothing is guessed."""
    if not isinstance(raw, dict):
        return None
    schedule: Schedule = {}
    for day in DAY_NAMES:
        raw_slots = raw.get(day)
        if not isinstance(raw_slots, list) or not raw_slots:
            return None
        slots = [_slot(raw_slot) for raw_slot in raw_slots]
        if any(slot is None for slot in slots):
            return None
        schedule[day] = sorted(slot for slot in slots if slot is not None)
    return schedule


def target_at(schedule: Schedule, moment: datetime) -> float:
    """The target of the slot in force at the local moment: the slot with the
    greatest start at or before its minutes after midnight on that weekday. Before
    the day's first slot, the previous day's last slot is still in force."""
    minutes = moment.hour * 60 + moment.minute
    started = [
        target
        for start, target in schedule[DAY_NAMES[moment.weekday()]]
        if start <= minutes
    ]
    if started:
        return started[-1]
    yesterday = moment - timedelta(days=1)
    return schedule[DAY_NAMES[yesterday.weekday()]][-1][1]


@dataclass(frozen=True)
class Part:
    """A share of an hour (0 to 1) held at one target, or at NO_TARGET."""

    share: float
    target: float


def schedule_parts(schedule: Schedule, hour_start: datetime) -> list[Part]:
    """The parts of the local hour at its start: the slot in force then, and a new
    part at each slot that starts strictly inside the hour."""
    first = hour_start.hour * 60 + hour_start.minute
    inside = [
        (start, target)
        for start, target in schedule[DAY_NAMES[hour_start.weekday()]]
        if first < start < first + MINUTES_PER_HOUR
    ]
    starts = [first, *(start for start, _ in inside), first + MINUTES_PER_HOUR]
    targets = [target_at(schedule, hour_start), *(target for _, target in inside)]
    return [
        Part((end - begin) / MINUTES_PER_HOUR, target)
        for begin, end, target in zip(starts, starts[1:], targets)
    ]


def _passive_change(
    numbers: ThermalNumbers,
    room: float,
    outdoor: float,
    radiation_kw: float,
    previous_fraction: float,
) -> float:
    """The room's change over an hour with no heating in it: what it loses to
    outdoors, what the previous hour's heating still gives, sunshine and the
    constant gain."""
    return (
        numbers.loss_per_degree * (outdoor - room)
        + numbers.carried_gain * previous_fraction
        + numbers.sun_gain * radiation_kw
        + numbers.constant_gain
    )


@dataclass(frozen=True)
class ForecastHour:
    """One forecast hour: its local wall-clock start and its weather."""

    start: datetime
    outdoor: float
    radiation_kw: float


def step_hour(
    numbers: ThermalNumbers,
    room: float,
    hour: ForecastHour,
    previous_fraction: float,
    parts: list[Part],
) -> tuple[float, float]:
    """The hour's heating fraction and the room at its end. The drift without
    heating is worked out once from the room at the hour's start and spread evenly;
    each part in turn heats the least that reaches its target, at most its share."""
    drift = _passive_change(
        numbers, room, hour.outdoor, hour.radiation_kw, previous_fraction
    )
    fraction = 0.0
    for part in parts:
        needed = (part.target - room - part.share * drift) / numbers.heating_gain
        heated = min(max(needed, 0.0), part.share)
        room += part.share * drift + numbers.heating_gain * heated
        fraction += heated
    return fraction, room


@dataclass(frozen=True)
class PlannedHour:
    """The room at an hour's start and the fraction of the hour it heats."""

    room: float
    fraction: float


@dataclass(frozen=True)
class Start:
    """The room now and the heating fraction of the hour before the first."""

    room: float
    previous_fraction: float


def _run(
    numbers: ThermalNumbers,
    start: Start,
    hours: list[ForecastHour],
    heating_of: Callable[[int, float, float], float],
) -> tuple[list[PlannedHour], Start]:
    """The hours stepped, each heating the fraction `heating_of(index, room,
    previous fraction)` gives, and where they leave the room."""
    planned = []
    room, previous = start.room, start.previous_fraction
    for index, hour in enumerate(hours):
        fraction = heating_of(index, room, previous)
        planned.append(PlannedHour(room, fraction))
        # The room at the hour's end: its passive change plus the heating chosen.
        room = _end_room(numbers, room, hour, previous, fraction)
        previous = fraction
    return planned, Start(room, previous)


def _end_room(
    numbers: ThermalNumbers,
    room: float,
    hour: ForecastHour,
    previous_fraction: float,
    fraction: float,
) -> float:
    passive = _passive_change(
        numbers, room, hour.outdoor, hour.radiation_kw, previous_fraction
    )
    return room + passive + numbers.heating_gain * fraction


def _hold(
    numbers: ThermalNumbers,
    start: Start,
    hours: list[ForecastHour],
    parts_of: Callable[[datetime], list[Part]],
) -> tuple[list[PlannedHour], Start]:
    planned = []
    room, previous = start.room, start.previous_fraction
    for hour in hours:
        fraction, end_room = step_hour(
            numbers, room, hour, previous, parts_of(hour.start)
        )
        planned.append(PlannedHour(room, fraction))
        room, previous = end_room, fraction
    return planned, Start(room, previous)


def hold_forecast(
    numbers: ThermalNumbers,
    start: Start,
    hours: list[ForecastHour],
    parts_of: Callable[[datetime], list[Part]],
) -> list[PlannedHour]:
    """Each hour stepped through the parts that `parts_of` gives for its local
    start."""
    return _hold(numbers, start, hours, parts_of)[0]


def round_up_to_half(temperature: float) -> float:
    """The temperature rounded up to the next 0.5 C (Hive's step); a tiny guard
    keeps float noise from lifting an exact half degree."""
    return math.ceil(temperature * 2 - 1e-9) / 2


def comfort_window(windows: ComfortWindows, day: date) -> tuple[datetime, datetime]:
    """The local start and end of the day's comfort window; weekends are Saturday
    and Sunday. An end at or before the start is the next day (00:00 is midnight at
    the end of the day)."""
    window = windows.weekend if day.weekday() >= SATURDAY else windows.weekday
    start = datetime.combine(day, window.start)
    end = datetime.combine(day, window.end)
    return start, end if end > start else end + timedelta(days=1)


def _with_fractions(
    numbers: ThermalNumbers,
    start: Start,
    hours: list[ForecastHour],
    fractions: list[float],
) -> tuple[list[PlannedHour], Start]:
    return _run(numbers, start, hours, lambda index, _room, _previous: fractions[index])


def _preheat_fractions(
    numbers: ThermalNumbers,
    start: Start,
    run_up: list[ForecastHour],
    window_start: datetime,
    target: float,
) -> list[float]:
    """The heating of the hours leading to the window: the shortest run of full
    heating, ending with the last of these hours, that brings the room to the target
    at the window's start (read by linear interpolation inside that last hour).
    The earliest hour of the run is fractional; if even heating throughout cannot
    get there, every hour heats."""
    count = len(run_up)

    def fractions_for(length: float) -> list[float]:
        return [
            min(max(length - (count - 1 - index), 0.0), 1.0) for index in range(count)
        ]

    def room_at_window(length: float) -> float:
        planned, after = _with_fractions(numbers, start, run_up, fractions_for(length))
        inside = min((window_start - run_up[-1].start) / ONE_HOUR, 1.0)
        return planned[-1].room + inside * (after.room - planned[-1].room)

    if room_at_window(0.0) >= target:
        return fractions_for(0.0)
    if room_at_window(float(count)) < target:
        return fractions_for(float(count))
    low, high = 0.0, float(count)
    for _ in range(BISECTIONS):
        middle = (low + high) / 2
        if room_at_window(middle) >= target:
            high = middle
        else:
            low = middle
    return fractions_for(high)


def comfort_plan(
    numbers: ThermalNumbers,
    start: Start,
    hours: list[ForecastHour],
    target: float,
    windows: ComfortWindows,
) -> list[PlannedHour]:
    """The forecast under heating that holds the target inside each day's comfort
    window, with the shortest pre-heat that gets there by its start, and none
    elsewhere. The hour a window starts in is the pre-heat's last, so it is not
    held, and the hour it ends in is left unheated. When the first hour is already
    in the window there is no pre-heat. The window that began the day before is
    planned first: an overnight window can still be open when the forecast starts."""
    days = list(dict.fromkeys(hour.start.date() for hour in hours))
    if not days:
        return []
    planned: list[PlannedHour] = []
    state = start
    for day in [days[0] - ONE_DAY, *days]:
        ran, state = _plan_day(
            numbers,
            state,
            hours[len(planned) :],
            target,
            comfort_window(windows, day),
        )
        planned += ran
    rest = hours[len(planned) :]
    return planned + _with_fractions(numbers, state, rest, [0.0] * len(rest))[0]


def _plan_day(
    numbers: ThermalNumbers,
    state: Start,
    remaining: list[ForecastHour],
    target: float,
    window: tuple[datetime, datetime],
) -> tuple[list[PlannedHour], Start]:
    """The pre-heat and the held hours of one window, from the hours not yet
    planned: the hours before the window are the pre-heat (heating or not)."""
    window_start, window_end = window
    first_held = next(
        (index for index, hour in enumerate(remaining) if hour.start >= window_start),
        len(remaining),
    )
    run_up = remaining[:first_held]
    # A window beyond the hours we have weather for gets no pre-heat.
    reachable = bool(run_up) and window_start - run_up[-1].start <= ONE_HOUR
    fractions = (
        _preheat_fractions(numbers, state, run_up, window_start, target)
        if reachable
        else [0.0] * len(run_up)
    )
    before, state = _with_fractions(numbers, state, run_up, fractions)
    # Whole hours only: the hour the window closes in starts at the target and
    # drifts little before it closes, and heating it all would overstate the cost.
    held = [
        hour for hour in remaining[first_held:] if hour.start + ONE_HOUR <= window_end
    ]
    during, state = _hold(numbers, state, held, lambda _start: [Part(1.0, target)])
    return before + during, state

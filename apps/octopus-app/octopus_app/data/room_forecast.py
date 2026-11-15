"""The room forecast's maths: Hive's weekly schedule read defensively, and one
hour of the thermal model stepped forward while holding a target (ADR-0035).
No I/O."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any

from octopus_app.common.config import ComfortWindows
from octopus_app.data.thermal import MINUTES_PER_HOUR, ONE_HOUR, ThermalNumbers

SATURDAY = 5
# Halvings of the pre-heat search: far below a minute.
BISECTIONS = 40
ONE_DAY = timedelta(days=1)
ZERO = timedelta(0)
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
    part at each slot that starts strictly inside the hour with a different target
    (a slot repeating the target changes nothing, and a boundary would make the
    room reach the target sooner than the whole hour does)."""
    first = hour_start.hour * 60 + hour_start.minute
    changes = [(first, target_at(schedule, hour_start))]
    for start, target in schedule[DAY_NAMES[hour_start.weekday()]]:
        if first < start < first + MINUTES_PER_HOUR and target != changes[-1][1]:
            changes.append((start, target))
    ends = [start for start, _ in changes[1:]] + [first + MINUTES_PER_HOUR]
    return [
        Part((end - begin) / MINUTES_PER_HOUR, target)
        for (begin, target), end in zip(changes, ends)
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
    """The room at the reading's local time `at` (the forecast's first hour starts
    there; None is the hour's start) and the heating fraction of the hour before the
    first. `seen_fraction` is the heating of the first hour already seen before `at`."""

    room: float
    previous_fraction: float
    at: datetime | None = None
    seen_fraction: float = 0.0


def _after(parts: list[Part], elapsed: float) -> list[Part]:
    """The parts of an hour after its first `elapsed` fraction has passed."""
    rest = []
    for part in parts:
        passed = min(part.share, elapsed)
        elapsed -= passed
        if part.share > passed:
            rest.append(Part(part.share - passed, part.target))
    return rest


def _from_reading(
    start: Start, index: int, hour: ForecastHour, parts: list[Part]
) -> tuple[list[Part], float]:
    """The parts of the hour to step and the heating already seen in it: for the
    first hour, only the parts after the reading and the seen heating."""
    if index == 0 and start.at is not None:
        return _after(parts, (start.at - hour.start) / ONE_HOUR), start.seen_fraction
    return parts, 0.0


def hold_forecast(
    numbers: ThermalNumbers,
    start: Start,
    hours: list[ForecastHour],
    parts_of: Callable[[datetime], list[Part]],
) -> list[PlannedHour]:
    """Each hour stepped through the parts that `parts_of` gives for its local
    start; the first only from the reading's time on."""
    planned = []
    room, previous = start.room, start.previous_fraction
    for index, hour in enumerate(hours):
        parts, seen = _from_reading(start, index, hour, parts_of(hour.start))
        fraction, end_room = step_hour(numbers, room, hour, previous, parts)
        planned.append(PlannedHour(room, fraction))
        room, previous = end_room, seen + fraction
    return planned


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


def _hour_parts(
    hour_start: datetime,
    held: list[tuple[datetime, datetime]],
    splits: list[datetime],
    target: float,
    upto: float = 1.0,
) -> list[Part]:
    """The first `upto` of the hour as parts: the target inside a held interval,
    none outside, and a new part wherever an interval or a split moment falls."""
    moments = [*splits, *(moment for interval in held for moment in interval)]
    marks = {0.0, upto}
    for moment in moments:
        mark = (moment - hour_start) / ONE_HOUR
        if 0.0 < mark < upto:
            marks.add(mark)
    ordered = sorted(marks)
    parts = []
    for begin, end in pairwise(ordered):
        middle = hour_start + (begin + end) / 2 * ONE_HOUR
        inside = any(opens <= middle < closes for opens, closes in held)
        parts.append(Part(end - begin, target if inside else NO_TARGET))
    return parts


def _room_at(
    numbers: ThermalNumbers,
    start: Start,
    hours: list[ForecastHour],
    parts_until: Callable[[datetime, float], list[Part]],
    moment: datetime,
) -> float:
    """The room at the moment: the hours before it stepped, and the hour it falls
    in stepped through the parts up to it."""
    room, previous = start.room, start.previous_fraction
    for index, hour in enumerate(hours):
        if hour.start >= moment:
            break
        upto = min((moment - hour.start) / ONE_HOUR, 1.0)
        parts, seen = _from_reading(start, index, hour, parts_until(hour.start, upto))
        fraction, room = step_hour(numbers, room, hour, previous, parts)
        previous = seen + fraction
    return room


def _lead(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    numbers: ThermalNumbers,
    start: Start,
    hours: list[ForecastHour],
    held: list[tuple[datetime, datetime]],
    splits: list[datetime],
    target: float,
    window: tuple[datetime, datetime],
) -> timedelta:
    """The shortest time before the window's opening, holding the target from then,
    that has the room at the target at the opening: none when it is already there
    or the window opens outside the forecast, all the time from the forecast's
    start when the target is out of reach."""
    opening, closing = window
    first = hours[0].start
    if not first < opening <= hours[-1].start + ONE_HOUR:
        return ZERO
    cap = opening - first

    def room_at_opening(share: float) -> float:
        interval = [*held, (opening - share * cap, closing)]
        return _room_at(
            numbers,
            start,
            hours,
            lambda hour_start, upto: _hour_parts(
                hour_start, interval, splits, target, upto
            ),
            opening,
        )

    if room_at_opening(0.0) >= target:
        return ZERO
    if room_at_opening(1.0) < target:
        return cap
    low, high = 0.0, 1.0
    for _ in range(BISECTIONS):
        middle = (low + high) / 2
        if room_at_opening(middle) >= target:
            high = middle
        else:
            low = middle
    return high * cap


def comfort_plan(
    numbers: ThermalNumbers,
    start: Start,
    hours: list[ForecastHour],
    target: float,
    windows: ComfortWindows,
) -> list[PlannedHour]:
    """The forecast under heating that holds the target from each comfort window's
    opening minute to its closing minute, with the shortest lead before the opening
    that has the room at the target then, and no heating elsewhere. The window that
    began the day before is planned too: an overnight window can still be open when
    the forecast starts."""
    if not hours:
        return []
    days = list(dict.fromkeys(hour.start.date() for hour in hours))
    spans = [comfort_window(windows, day) for day in [days[0] - ONE_DAY, *days]]
    splits = [opening for opening, _ in spans]
    held: list[tuple[datetime, datetime]] = []
    for window in spans:
        if window[1] > hours[0].start:
            lead = _lead(numbers, start, hours, held, splits, target, window)
            held.append((window[0] - lead, window[1]))
    return hold_forecast(
        numbers,
        start,
        hours,
        lambda hour_start: _hour_parts(hour_start, held, splits, target),
    )

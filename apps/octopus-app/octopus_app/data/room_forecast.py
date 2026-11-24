"""The room forecast's maths: Hive's weekly schedule read defensively, and the
thermal model stepped forward in 2-minute steps while holding a target
(ADR-0035, ADR-0036). No I/O."""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from octopus_app.common.config import ComfortWindows
from octopus_app.data.thermal import MINUTES_PER_HOUR, ONE_HOUR, ThermalNumbers

SATURDAY = 5
STEP_MINUTES = 2  # the thermostat's reading interval (ADR-0036)
STEP = timedelta(minutes=STEP_MINUTES)
STEPS_PER_HOUR = MINUTES_PER_HOUR // STEP_MINUTES
ONE_DAY = timedelta(days=1)
ZERO = timedelta(0)
# A target no room is below: the heat needed clamps to zero.
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


def schedule_target_at_step(schedule: Schedule, step_start: datetime) -> float:
    """The target of the slot in force at a 2-minute step's start. A slot starting
    on an odd minute takes effect from the step before it."""
    return target_at(schedule, step_start + timedelta(minutes=1))


@dataclass(frozen=True)
class Slot:
    """A span of a local day held at one target (naive local times)."""

    start: datetime
    end: datetime
    target: float


def schedule_slots(schedule: Schedule, day: date) -> list[Slot]:
    """The day's slots: each runs to the next one's start and the last to 00:00
    next day. Before the day's first start the previous day's last target holds,
    and consecutive equal targets merge."""
    midnight = datetime(day.year, day.month, day.day)
    changes = [(0, target_at(schedule, midnight))]
    for start, target in schedule[DAY_NAMES[day.weekday()]]:
        if start > 0 and target != changes[-1][1]:
            changes.append((start, target))
    ends = [start for start, _ in changes[1:]] + [24 * MINUTES_PER_HOUR]
    return [
        Slot(
            midnight + timedelta(minutes=begin),
            midnight + timedelta(minutes=end),
            target,
        )
        for (begin, target), end in zip(changes, ends)
    ]


@dataclass(frozen=True)
class RoomModel:
    """The thermal model as the forecast uses it: its five numbers (per hour),
    the step the numbers were fitted on (60 for a model fitted hourly) and the
    Warm-Up Delay."""

    numbers: ThermalNumbers
    step_minutes: int = MINUTES_PER_HOUR
    delay_minutes: int = 0

    @property
    def delay_steps(self) -> int:
        return round(self.delay_minutes / STEP_MINUTES)

    @property
    def carried_steps(self) -> int:
        return self.step_minutes // STEP_MINUTES


@dataclass(frozen=True)
class ForecastHour:
    """One forecast hour: its local wall-clock start and its weather. Outdoor
    temperature runs in a straight line from `outdoor` to `next_outdoor`; the
    radiation is the hour's mean."""

    start: datetime
    outdoor: float
    next_outdoor: float
    radiation_kw: float


@dataclass(frozen=True)
class Start:
    """The room at the reading, and the heat the thermostat demanded before it:
    minutes demanded in each 2-minute step before the first forecast step, oldest
    first (the delay plus the model's step long; missing steps count as none)."""

    room: float
    at: datetime
    recorded_demand: Sequence[float] = ()


@dataclass(frozen=True)
class PlannedStep:
    """A 2-minute step `offset_minutes` after the first forecast hour's start: the
    room at its start and the minutes of heat demanded in it."""

    offset_minutes: int
    room: float
    demand_minutes: float


def first_step_start(start: Start, hours: list[ForecastHour]) -> datetime:
    """The local start of the step containing the reading."""
    return hours[0].start + (start.at - hours[0].start) // STEP * STEP


def _label(hours: list[ForecastHour], offset_minutes: int) -> datetime:
    """The local wall-clock start of the step at an offset from the first hour's
    start; the hour it falls in keeps its own label, so a repeated clock hour is
    read twice."""
    index = min(offset_minutes // MINUTES_PER_HOUR, len(hours) - 1)
    return hours[index].start + timedelta(
        minutes=offset_minutes - index * MINUTES_PER_HOUR
    )


def _weather_at(hours: list[ForecastHour], offset_minutes: int) -> tuple[float, float]:
    """The outdoor temperature and radiation (kW/m2) at a step's start; beyond the
    last hour the temperature holds at its end value."""
    index = min(offset_minutes // MINUTES_PER_HOUR, len(hours) - 1)
    hour = hours[index]
    fraction = min((offset_minutes - index * MINUTES_PER_HOUR) / MINUTES_PER_HOUR, 1.0)
    return (
        hour.outdoor + fraction * (hour.next_outdoor - hour.outdoor),
        hour.radiation_kw,
    )


def _passive_change(
    model: RoomModel,
    room: float,
    weather: tuple[float, float],
    carried_minutes: float,
) -> float:
    """The room's change over a step with no heat arriving in it: what it loses to
    outdoors, sunshine, the constant gain and what the heat that arrived in the
    model's step before still gives."""
    numbers = model.numbers
    outdoor, radiation_kw = weather
    share_of_hour = STEP_MINUTES / MINUTES_PER_HOUR
    return (
        share_of_hour
        * (
            numbers.loss_per_degree * (outdoor - room)
            + numbers.sun_gain * radiation_kw
            + numbers.constant_gain
        )
        + numbers.carried_gain
        * (STEP_MINUTES / model.step_minutes)
        * carried_minutes
        / MINUTES_PER_HOUR
    )


def _arrival_needed(
    model: RoomModel, room: float, target: float, passive_change: float
) -> float:
    """The minutes of arriving heat in a step that leave the room at the target at
    its end, at most the whole step and at least none."""
    needed = (target - room - passive_change) * MINUTES_PER_HOUR
    return min(max(needed / model.numbers.heating_gain, 0.0), float(STEP_MINUTES))


def _simulate(  # pylint: disable=too-many-locals
    model: RoomModel,
    start: Start,
    hours: list[ForecastHour],
    target_of: Callable[[datetime], float],
    step_count: int | None = None,
) -> tuple[list[PlannedStep], float]:
    """The forecast in 2-minute steps from the one containing the reading, and the
    room at the end of the last. Heat demanded in a step arrives `delay` later, so
    the heat arriving in a step is sized to the target of the step it was demanded
    in (and is the recorded demand while that step is before the forecast). Without
    a `step_count`, it runs to the end of the hours."""
    delay, window = model.delay_steps, model.carried_steps
    first = (first_step_start(start, hours) - hours[0].start) // STEP
    count = len(hours) * STEPS_PER_HOUR - first if step_count is None else step_count
    # Steps beyond the forecast only settle the demand of its last `delay` steps.
    settling = delay if step_count is None else 0
    recorded = start.recorded_demand

    def recorded_at(index: int) -> float:
        position = len(recorded) + index
        return recorded[position] if 0 <= position < len(recorded) else 0.0

    arrived = {index: recorded_at(index - delay) for index in range(-window, 0)}
    demand = [0.0] * count
    room_at_start: list[float] = []
    room = end_room = start.room
    for step in range(count + settling):
        offset = (first + step) * STEP_MINUTES
        carried = sum(arrived[earlier] for earlier in range(step - window, step))
        passive = _passive_change(model, room, _weather_at(hours, offset), carried)
        demanded_at = step - delay
        if demanded_at < 0:
            heat = recorded_at(demanded_at)
        else:
            target = target_of(_label(hours, (first + demanded_at) * STEP_MINUTES))
            heat = _arrival_needed(model, room, target, passive)
            demand[demanded_at] = heat
        arrived[step] = heat
        if step < count:
            room_at_start.append(room)
        room += passive + model.numbers.heating_gain * heat / MINUTES_PER_HOUR
        if step + 1 == count:
            end_room = room
    steps = [
        PlannedStep((first + index) * STEP_MINUTES, room_start, demand[index])
        for index, room_start in enumerate(room_at_start)
    ]
    return steps, end_room


def hold_forecast(
    model: RoomModel,
    start: Start,
    hours: list[ForecastHour],
    target_of: Callable[[datetime], float],
) -> list[PlannedStep]:
    """Each 2-minute step from the reading to the end of the hours, heating the
    least that holds the target `target_of` gives for each step's local start."""
    if not hours:
        return []
    return _simulate(model, start, hours, target_of)[0]


def comfort_window(windows: ComfortWindows, day: date) -> tuple[datetime, datetime]:
    """The local start and end of the day's comfort window; weekends are Saturday
    and Sunday. An end at or before the start is the next day (00:00 is midnight at
    the end of the day)."""
    window = windows.weekend if day.weekday() >= SATURDAY else windows.weekday
    start = datetime.combine(day, window.start)
    end = datetime.combine(day, window.end)
    return start, end if end > start else end + timedelta(days=1)


def _steps_before(start: Start, hours: list[ForecastHour], moment: datetime) -> int:
    """The forecast steps whose local start is before the moment. Counted by the
    steps themselves, not by the local clock, so a clock change before the moment
    does not count a skipped hour."""
    first = (first_step_start(start, hours) - hours[0].start) // STEP
    count = 0
    for index in range(first, len(hours) * STEPS_PER_HOUR):
        if _label(hours, index * STEP_MINUTES) >= moment:
            break
        count += 1
    return count


def _target_in(
    held: list[tuple[datetime, datetime]], target: float
) -> Callable[[datetime], float]:
    """The target inside a held interval and none outside."""

    def target_of(local: datetime) -> float:
        inside = any(opens <= local < closes for opens, closes in held)
        return target if inside else NO_TARGET

    return target_of


def _lead(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    model: RoomModel,
    start: Start,
    hours: list[ForecastHour],
    held: list[tuple[datetime, datetime]],
    target: float,
    window: tuple[datetime, datetime],
) -> timedelta:
    """The shortest time, in whole 2-minute steps, before the window's opening,
    holding the target from then, that has the room at the target at the opening:
    none when it is already there or the window opens outside the forecast, all
    the time from the forecast's start when the target is out of reach. Heat
    arrives a Warm-Up Delay late, so the lead includes the delay."""
    opening, closing = window
    first = first_step_start(start, hours)
    if not first < opening <= hours[-1].start + ONE_HOUR:
        return ZERO
    # The simulation runs the steps that elapse before the opening, but the lead is
    # taken off the wall clock, so it can reach back to the forecast's first step
    # even when a clock change skips an hour in between.
    elapsed_steps = _steps_before(start, hours, opening)
    cap = (opening - first) // STEP

    def room_at_opening(lead_steps: int) -> float:
        interval = [*held, (opening - lead_steps * STEP, closing)]
        return _simulate(
            model, start, hours, _target_in(interval, target), elapsed_steps
        )[1]

    if room_at_opening(0) >= target:
        return ZERO
    if room_at_opening(cap) < target:
        return cap * STEP
    low, high = 0, cap  # the room misses with `low` steps and reaches it with `high`
    while high - low > 1:
        middle = (low + high) // 2
        if room_at_opening(middle) >= target:
            high = middle
        else:
            low = middle
    return high * STEP


def _held_spans(
    model: RoomModel,
    start: Start,
    hours: list[ForecastHour],
    target: float,
    windows: ComfortWindows,
) -> tuple[list[tuple[datetime, datetime]], list[date]]:
    """Per comfort window still open at the forecast's start, the span held:
    (opening minus its lead, closing); and the day each held span's window opens
    on, aligned with the spans. The window that began the day before is included:
    an overnight window can still be open when the forecast starts."""
    days = list(dict.fromkeys(hour.start.date() for hour in hours))
    window_days = [days[0] - ONE_DAY, *days]
    held: list[tuple[datetime, datetime]] = []
    opening_days: list[date] = []
    for day in window_days:
        window = comfort_window(windows, day)
        if window[1] > hours[0].start:
            lead = _lead(model, start, hours, held, target, window)
            held.append((window[0] - lead, window[1]))
            opening_days.append(day)
    return held, opening_days


def comfort_plan(
    model: RoomModel,
    start: Start,
    hours: list[ForecastHour],
    target: float,
    windows: ComfortWindows,
) -> list[PlannedStep]:
    """The forecast under heating that holds the target from each comfort window's
    opening, less its lead, to its closing, with the shortest lead that has the
    room at the target at the opening, and no heating elsewhere."""
    if not hours:
        return []
    held, _ = _held_spans(model, start, hours, target, windows)
    return hold_forecast(model, start, hours, _target_in(held, target))


def plan_slots(
    model: RoomModel,
    start: Start,
    hours: list[ForecastHour],
    target: float,
    windows: ComfortWindows,
) -> list[tuple[date, Slot]]:
    """The comfort plan's held span (pre-heat start to window close) of each window
    overlapping the forecast's first day or the next, at the target as given, each
    with the local date its window opens on (naive local times)."""
    if not hours:
        return []
    held, opening_days = _held_spans(model, start, hours, target, windows)
    first = datetime.combine(hours[0].start.date(), datetime.min.time())
    return [
        (day, Slot(opens, closes, target))
        for day, (opens, closes) in zip(opening_days, held)
        if first < closes and opens < first + 2 * ONE_DAY
    ]

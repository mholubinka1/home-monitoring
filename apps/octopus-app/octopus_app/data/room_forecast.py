"""The room forecast's maths: Hive's weekly schedule read defensively, and one
hour of the thermal model stepped forward while holding a target (ADR-0035).
No I/O."""

import math
from datetime import datetime, timedelta
from typing import Any

from octopus_app.data.thermal import ThermalNumbers

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


def hold_target(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    numbers: ThermalNumbers,
    room: float,
    target: float,
    outdoor: float,
    radiation_kw: float,
    previous_fraction: float,
) -> tuple[float, float]:
    """The smallest fraction of the hour's heating (0 to 1) that holds the room at
    the target, and the room at the hour's end."""
    passive = (
        numbers.loss_per_degree * (outdoor - room)
        + numbers.carried_gain * previous_fraction
        + numbers.sun_gain * radiation_kw
        + numbers.constant_gain
    )
    needed = (target - room - passive) / numbers.heating_gain
    fraction = min(max(needed, 0.0), 1.0)
    return fraction, room + passive + numbers.heating_gain * fraction

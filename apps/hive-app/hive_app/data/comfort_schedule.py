"""Builds the week Hive is told to hold: Hive's own weekly schedule with today and
tomorrow replaced by the Comfort Plan, where the plan has slots for them."""

import copy
from datetime import UTC, date, datetime, timedelta
from typing import Any

from common.local_day import get_local_timezone

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
MINUTES_IN_DAY = 1440


def _entry(start: int, target: float) -> dict[str, Any]:
    return {"start": start, "value": {"target": float(target)}}


def _london_minutes(instant: datetime, round_up: bool = False) -> int:
    """Whole minutes since local midnight of a naive-UTC instant, rounded down,
    or up when round_up, so the Comfort Window is never shortened."""
    local = instant.replace(tzinfo=UTC).astimezone(get_local_timezone())
    minutes = local.hour * 60 + local.minute
    if round_up and (local.second or local.microsecond):
        minutes += 1
    return minutes


def build_week(
    hive_week: dict[str, Any],
    plan_slots: list[tuple[date, datetime, datetime, float]],
    today: date,
    away_temperature: float,
) -> dict[str, Any]:
    """A copy of Hive's week with today and tomorrow replaced by their plan
    slots. A day with no plan slot keeps Hive's entries."""
    week = copy.deepcopy(hive_week)
    for day in (today, today + timedelta(days=1)):
        slots = sorted(s for s in plan_slots if s[0] == day)
        if not slots:
            continue
        entries = [_entry(0, away_temperature)]
        for _, starts_at, ends_at, target in slots:
            entries.append(_entry(_london_minutes(starts_at), target))
            end = _london_minutes(ends_at, round_up=True)
            if end % MINUTES_IN_DAY != 0:
                entries.append(_entry(end, away_temperature))
        week[WEEKDAYS[day.weekday()]] = entries
    return week


def validate_week(week: dict[str, Any]) -> None:
    """Refuse a week Hive must not be sent: it must hold exactly the seven
    weekdays, each with entries at distinct whole minutes of the day starting at
    midnight, with targets in half degrees. The messages hold no secrets."""
    if set(week) != set(WEEKDAYS):
        raise ValueError("the week must hold exactly the seven lowercase weekdays")
    for day, entries in week.items():
        if not entries:
            raise ValueError(f"{day} has no entries")
        starts = [entry["start"] for entry in entries]
        if any(
            isinstance(start, bool)
            or not isinstance(start, int)
            or not 0 <= start < MINUTES_IN_DAY
            for start in starts
        ):
            raise ValueError(f"{day} has a start that is not a minute of the day")
        if len(set(starts)) != len(starts):
            raise ValueError(f"{day} has two entries starting at the same minute")
        if min(starts) != 0:
            raise ValueError(f"{day} does not start at midnight")
        for entry in entries:
            target = entry["value"]["target"]
            if isinstance(target, bool) or not isinstance(target, int | float):
                # ValueError, like every other refusal here: callers catch one type.
                raise ValueError(  # noqa: TRY004
                    f"{day} has a target that is not a number"
                )
            if (target * 2) % 1 != 0:
                raise ValueError(f"{day} has a target that is not in half degrees")

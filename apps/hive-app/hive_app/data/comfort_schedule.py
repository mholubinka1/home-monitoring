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
) -> tuple[dict[str, Any], list[str]]:
    """A copy of Hive's week with today and tomorrow replaced by their plan
    slots, and the weekdays among them that kept Hive's entries: a day with no
    plan slot keeps them."""
    week = copy.deepcopy(hive_week)
    kept: list[str] = []
    for day in (today, today + timedelta(days=1)):
        slots = sorted(s for s in plan_slots if s[0] == day)
        if not slots:
            kept.append(WEEKDAYS[day.weekday()])
            continue
        entries = [_entry(0, away_temperature)]
        for _, starts_at, ends_at, target in slots:
            entries.append(_entry(_london_minutes(starts_at), target))
            end = _london_minutes(ends_at, round_up=True)
            if end % MINUTES_IN_DAY != 0:
                entries.append(_entry(end, away_temperature))
        week[WEEKDAYS[day.weekday()]] = entries
    return week, kept


class WeekRuleError(ValueError):
    """A week breaks a write rule; its message names a weekday and the rule only,
    never input values."""


def _is_well_formed_entry(entry: object) -> bool:
    """An entry is a dict with a start and a value dict holding a target."""
    return (
        isinstance(entry, dict)
        and "start" in entry
        and isinstance(entry.get("value"), dict)
        and "target" in entry["value"]
    )


def validate_week(week: dict[str, Any]) -> None:
    """Refuse a week Hive must not be sent: it must hold exactly the seven
    weekdays, each a list of entries at distinct whole minutes of the day starting
    at midnight, with targets in half degrees. A malformed week raises WeekRuleError
    too, never KeyError or TypeError. The messages name a weekday and a rule only."""
    if set(week) != set(WEEKDAYS):
        raise WeekRuleError("the week must hold exactly the seven lowercase weekdays")
    for day, entries in week.items():
        if not isinstance(entries, list):
            raise WeekRuleError(f"{day} is not a list of entries")
        if not entries:
            raise WeekRuleError(f"{day} has no entries")
        if not all(_is_well_formed_entry(entry) for entry in entries):
            raise WeekRuleError(f"{day} has an entry without a start and a target")
        starts = [entry["start"] for entry in entries]
        if any(
            isinstance(start, bool)
            or not isinstance(start, int)
            or not 0 <= start < MINUTES_IN_DAY
            for start in starts
        ):
            raise WeekRuleError(f"{day} has a start that is not a minute of the day")
        if len(set(starts)) != len(starts):
            raise WeekRuleError(f"{day} has two entries starting at the same minute")
        if min(starts) != 0:
            raise WeekRuleError(f"{day} does not start at midnight")
        for entry in entries:
            target = entry["value"]["target"]
            if isinstance(target, bool) or not isinstance(target, int | float):
                raise WeekRuleError(f"{day} has a target that is not a number")
            if (target * 2) % 1 != 0:
                raise WeekRuleError(f"{day} has a target that is not in half degrees")

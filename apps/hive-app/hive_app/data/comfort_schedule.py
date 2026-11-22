"""Builds the week Hive is told to hold: Hive's own weekly schedule with today and
tomorrow replaced by the Comfort Plan, where the plan has slots for them."""

import copy
import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
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
# Why a day kept Hive's entries.
REASON_NO_SLOT = "no plan slot"
REASON_OUTSIDE_DAY = "its plan slot does not lie within the day"


def _entry(start: int, target: float) -> dict[str, Any]:
    return {"start": start, "value": {"target": float(target)}}


def _local(instant: datetime) -> datetime:
    """A naive-UTC instant on the local clock, with its own offset."""
    return instant.replace(tzinfo=UTC).astimezone(get_local_timezone())


def _lies_within(day: date, starts_at: datetime, ends_at: datetime) -> bool:
    """Whether a slot starts on the local day and ends on it or at the next
    local midnight exactly."""
    start, end = _local(starts_at), _local(ends_at)
    if start.date() != day:
        return False
    next_midnight = end.date() == day + timedelta(days=1) and end.time() == time(0)
    return end.date() == day or next_midnight


def _london_minutes(instant: datetime, round_up: bool = False) -> int:
    """Whole minutes since local midnight of a naive-UTC instant, rounded down,
    or up when round_up, so the Comfort Window is never shortened."""
    local = _local(instant)
    minutes = local.hour * 60 + local.minute
    if round_up and (local.second or local.microsecond):
        minutes += 1
    return minutes


def build_week(
    hive_week: dict[str, Any],
    plan_slots: list[tuple[date, datetime, datetime, float]],
    today: date,
    away_temperature: float,
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """A copy of Hive's week with today and tomorrow replaced by their plan
    slots, and the (weekday, reason) of those that kept Hive's entries: a day
    with no plan slot, or any slot not within the day, keeps them. A replaced day
    holds only its slots' starts and closes, with no entry invented at midnight."""
    week = copy.deepcopy(hive_week)
    kept: list[tuple[str, str]] = []
    for day in (today, today + timedelta(days=1)):
        slots = sorted(s for s in plan_slots if s[0] == day)
        if not slots:
            kept.append((WEEKDAYS[day.weekday()], REASON_NO_SLOT))
            continue
        if not all(_lies_within(day, s[1], s[2]) for s in slots):
            kept.append((WEEKDAYS[day.weekday()], REASON_OUTSIDE_DAY))
            continue
        entries: list[dict[str, Any]] = []
        for _, starts_at, ends_at, target in slots:
            entries.append(_entry(_london_minutes(starts_at), target))
            end = _london_minutes(ends_at, round_up=True)
            # A start must be 0-1439, so a slot ending at midnight closes at 23:59.
            close = end % MINUTES_IN_DAY or MINUTES_IN_DAY - 1
            entries.append(_entry(close, away_temperature))
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
    weekdays, each a list of entries at distinct whole minutes of the day,
    with targets in half degrees. A malformed week raises WeekRuleError
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
        for entry in entries:
            target = entry["value"]["target"]
            if isinstance(target, bool) or not isinstance(target, int | float):
                raise WeekRuleError(f"{day} has a target that is not a number")
            if (target * 2) % 1 != 0:
                raise WeekRuleError(f"{day} has a target that is not in half degrees")


def fingerprint_week(week: dict[str, Any]) -> str:
    """A stable SHA-256 hex digest of a week, to tell one from another: entries
    sorted by start and targets as floats, so equal weeks match however Hive
    orders or types them. Only `start` and `value.target` count: that is the
    whole of each entry Hive returns (spike #807), so any other field would be
    ignored. A malformed week (as Hive could return) is hashed as it is, so it
    never matches a well-formed one and never raises."""
    try:
        normalised: Any = {
            day: [
                {
                    "start": entry["start"],
                    "value": {"target": float(entry["value"]["target"])},
                }
                for entry in sorted(entries, key=lambda entry: entry["start"])
            ]
            for day, entries in week.items()
        }
    except (AttributeError, KeyError, TypeError, ValueError):
        normalised = week
    return hashlib.sha256(
        json.dumps(normalised, sort_keys=True, default=str).encode()
    ).hexdigest()

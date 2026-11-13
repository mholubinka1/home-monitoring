"""The comfort temperature: the room temperature at which the household usually
reaches for boost (ADR-0034)."""

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from math import isclose

from common import local_day
from octopus_app.data.heating_hours import POLL_MINUTES

BOOST_WINDOW_DAYS = 730  # only bounds the read: a boost is dropped at ~1.6% weight
COUNTED_HOURS_WINDOW_DAYS = 365
HALF_LIFE_HEATING_DAYS = 60
MIN_BOOST_STARTS = 5
BOOST_GAP = timedelta(minutes=10)

BoostStart = tuple[datetime, float]


def boost_starts(
    boost_polls: Iterable[tuple[datetime, float | None]],
) -> list[BoostStart]:
    """The (time, room temperature) at which each boost started, from the polls
    with boost active, oldest first. A poll starts a boost when it is more than
    10 minutes after the previous boost poll; a start with no room temperature
    is left out."""
    starts: list[BoostStart] = []
    previous: datetime | None = None
    for polled_at, room in boost_polls:
        if (previous is None or polled_at - previous > BOOST_GAP) and room is not None:
            starts.append((polled_at, room))
        previous = polled_at
    return starts


def heating_days(heating_on_hours: Iterable[datetime]) -> list[date]:
    """The local days, ascending, on which the heating was on, from the hours
    (naive UTC starts) in which it was."""
    return sorted({local_day.to_local_date(hour) for hour in heating_on_hours})


def boost_weight(days_with_heating: list[date], boost_day: date, day: date) -> float:
    """How much a boost on boost_day counts towards day's comfort temperature:
    half as much for every 60 heating days between the two. Days without heating
    (a summer) age nothing."""
    heating_days_since = bisect_left(days_with_heating, day) - bisect_right(
        days_with_heating, boost_day
    )
    return 0.5 ** (heating_days_since / HALF_LIFE_HEATING_DAYS)


def comfort_temperature(
    weighted_start_temps: list[tuple[float, float]], carried: float | None
) -> float | None:
    """The weighted median of the (room temperature, weight) boost starts; with
    under 5 of them, the last learned value carries on."""
    if len(weighted_start_temps) < MIN_BOOST_STARTS:
        return carried
    return _weighted_median(weighted_start_temps)


def _weighted_median(weighted_values: list[tuple[float, float]]) -> float:
    """The lowest value at which the running weight reaches half the total, or
    its midpoint with the next value when the running weight is exactly half, so
    equal weights give the plain median."""
    ordered = sorted(weighted_values)
    half = sum(weight for _, weight in ordered) / 2
    running = 0.0
    for index, (value, weight) in enumerate(ordered):
        running += weight
        if isclose(running, half) and index + 1 < len(ordered):
            return (value + ordered[index + 1][0]) / 2
        if running > half:
            return value
    return ordered[-1][0]


def counted_hours(heating_on_hours: Iterable[datetime]) -> list[int]:
    """The local hours of day, ascending, of the hours (naive UTC starts) in which
    the heating was on."""
    return sorted({local_day.to_local_hour(hour) for hour in heating_on_hours})


def minutes_below(
    polls: Iterable[tuple[datetime, float | None]],
    comfort_temp: float,
    hours: Iterable[int],
) -> int:
    """The minutes of the (time, room temperature) polls in which the room was
    below the comfort temperature during a counted hour."""
    counted = set(hours)
    return POLL_MINUTES * sum(
        room is not None
        and room < comfort_temp
        and local_day.to_local_hour(polled_at) in counted
        for polled_at, room in polls
    )

"""The comfort temperature: a value that starts each day at the one in force at the
end of the day before and steps up by half a degree after a Held Boost (ADR-0040)."""

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from math import ceil

from common import local_day
from octopus_app.data.heating_intervals import POLL_MINUTES
from octopus_app.data.model import ComfortDayRecord, ThermostatPoll

COUNTED_HOURS_WINDOW_DAYS = 365
BOOST_GAP = timedelta(minutes=10)
# Deliberately hardcoded, not configurable (ADR-0040).
COMFORT_FLOOR = 16.0
COMFORT_STEP = 0.5
HELD_MARGIN = 0.5

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


def _held_before(
    previous: ThermostatPoll, boost: ThermostatPoll, comfort_temp: float
) -> bool:
    """Whether the poll before a boost held the comfort temperature (in half
    degrees) and the boost started with the room within the margin of it."""
    return (
        previous.target_temp is not None
        and round(previous.target_temp * 2) == round(comfort_temp * 2)
        and boost.current_temp is not None
        and boost.current_temp >= comfort_temp - HELD_MARGIN
    )


def held_boost_start(
    polls: Sequence[ThermostatPoll],
    comfort_temp: float,
    window: tuple[datetime, datetime],
) -> datetime | None:
    """When the first Held Boost started, if any, from the day's polls (oldest
    first, from one boost gap before midnight) and its Comfort Window (naive UTC).
    A Held Boost is a Boost Start (as in boost_starts) inside the window, whose
    poll just before it, at most a boost gap earlier, held the comfort temperature
    (in half degrees), with the room within the margin of it."""
    opens, closes = window
    previous: ThermostatPoll | None = None
    previous_boost: datetime | None = None
    for poll in polls:
        if poll.boost_active:
            starts = (
                previous_boost is None or poll.polled_at - previous_boost > BOOST_GAP
            )
            previous_boost = poll.polled_at
            if (
                starts
                and previous is not None
                and opens <= poll.polled_at < closes
                and poll.polled_at - previous.polled_at <= BOOST_GAP
                and _held_before(previous, poll, comfort_temp)
            ):
                return poll.polled_at
        previous = poll
    return None


def round_up_to_half(temperature: float) -> float:
    """The temperature rounded up to the next 0.5 C (Hive's step); a tiny guard
    keeps float noise from lifting an exact half degree."""
    return ceil(temperature * 2 - 1e-9) / 2


def value_in_force_at_end(row: ComfortDayRecord | None) -> float:
    """The comfort temperature in force at the end of a stored day: raised by a
    step if it was raised; a row from before the stepped rule (no quiet count)
    rounded up to a half degree; the floor with no row."""
    if row is None or row.comfort_temp is None:
        return COMFORT_FLOOR
    if row.raised_at is not None:
        return row.comfort_temp + COMFORT_STEP
    if row.quiet_count is None:
        return round_up_to_half(row.comfort_temp)
    return row.comfort_temp


def counted_hours(heating_on_hours: Iterable[datetime]) -> list[int]:
    """The local hours of day, ascending, of the hours (naive UTC starts) in which
    the heating was on."""
    return sorted({local_day.to_local_hour(hour) for hour in heating_on_hours})


def minutes_below(
    polls: Iterable[tuple[datetime, float | None]],
    comfort_temp: float,
    hours: Iterable[int],
    no_one_home: list[tuple[datetime, datetime | None]],
    raised_at: datetime | None = None,
) -> int:
    """The minutes of the (time, room temperature) polls in which the room was
    below the value in force (the comfort temperature, a step more from raised_at on)
    during a counted hour, leaving out polls inside
    a (started_at, ended_at) no_one_home period; ended_at None is still open."""
    counted = set(hours)
    return POLL_MINUTES * sum(
        room is not None
        and room
        < comfort_temp
        + (COMFORT_STEP if raised_at is not None and polled_at >= raised_at else 0)
        and local_day.to_local_hour(polled_at) in counted
        and not any(
            started <= polled_at and (ended is None or polled_at < ended)
            for started, ended in no_one_home
        )
        for polled_at, room in polls
    )

"""The comfort temperature: a value that starts each day at the one in force at the
end of the day before and steps up by half a degree after a Held Boost (ADR-0040)."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from typing import Literal

from common import local_day
from octopus_app.data.heating_intervals import POLL_MINUTES
from octopus_app.data.model import ComfortDayRecord, ThermostatPoll

COUNTED_HOURS_WINDOW_DAYS = 365
BOOST_GAP = timedelta(minutes=10)
# Deliberately hardcoded, not configurable (ADR-0040).
COMFORT_FLOOR = 16.0
# The gas dashboard's comfort query (query D) repeats this step as 0.5.
COMFORT_STEP = 0.5
# How far below the comfort temperature a Held Boost's room may be, and how far
# below it a reading must be to count as minutes below comfort.
HELD_MARGIN = 0.5
QUIET_DAYS_PER_STEP = 3
QUIET_MINUTES = 120

Period = tuple[datetime, datetime | None]  # (started_at, ended_at); None is open
DayKind = Literal["held_boost", "quiet"] | None


def _reachable(poll: ThermostatPoll) -> bool:
    """Whether Hive could reach the thermostat for the poll: an offline poll
    repeats stale values, so it is never evidence. NULL (older rows) is online."""
    return poll.online is not False


def _holds(poll: ThermostatPoll, comfort_temp: float) -> bool:
    """Whether the poll shows Hive holding the comfort temperature, in half
    degrees, with the thermostat reachable."""
    return (
        _reachable(poll)
        and poll.target_temp is not None
        and round(poll.target_temp * 2) == round(comfort_temp * 2)
    )


def _within(at: datetime, periods: Iterable[Period]) -> bool:
    """Whether the instant falls inside any of the periods."""
    return any(
        started <= at and (ended is None or at < ended) for started, ended in periods
    )


def _starts_boost(previous_boost: datetime | None, polled_at: datetime) -> bool:
    """Whether a boost poll starts a boost: more than a boost gap after the
    previous boost poll, or the first one read."""
    return previous_boost is None or polled_at - previous_boost > BOOST_GAP


def boost_starts(boost_poll_times: Iterable[datetime]) -> list[datetime]:
    """When each boost started, from the times of the polls with boost active,
    oldest first. A poll starts a boost when it is more than 10 minutes after the
    previous boost poll, whether or not it has a room reading."""
    starts: list[datetime] = []
    previous: datetime | None = None
    for polled_at in boost_poll_times:
        if _starts_boost(previous, polled_at):
            starts.append(polled_at)
        previous = polled_at
    return starts


def _held_before(
    previous: ThermostatPoll, boost: ThermostatPoll, comfort_temp: float
) -> bool:
    """Whether the poll before a boost held the comfort temperature (in half
    degrees) and the boost started with the room below it, by no more than the
    margin: a room already at temperature is no evidence it is too cold."""
    return (
        _holds(previous, comfort_temp)
        and boost.current_temp is not None
        and comfort_temp - HELD_MARGIN <= boost.current_temp < comfort_temp
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
            starts = _starts_boost(previous_boost, poll.polled_at)
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


def is_quiet_day(
    polls: Iterable[ThermostatPoll],
    comfort_temp: float,
    window: tuple[datetime, datetime],
    no_one_home: list[Period],
) -> bool:
    """Whether a day was quiet: no boost started in the Comfort Window (naive
    UTC), Hive held the comfort temperature (in half degrees) for at least
    QUIET_MINUTES of it with someone home, and the heating was on at some point
    in it."""
    opens, closes = window
    polls = list(polls)
    if any(
        opens <= start < closes
        for start in boost_starts(poll.polled_at for poll in polls if poll.boost_active)
    ):
        return False
    in_window = [poll for poll in polls if opens <= poll.polled_at < closes]
    held_minutes = POLL_MINUTES * sum(
        _holds(poll, comfort_temp) and not _within(poll.polled_at, no_one_home)
        for poll in in_window
    )
    return held_minutes >= QUIET_MINUTES and any(
        _reachable(poll) and (poll.state == "ON" or poll.boost_active)
        for poll in in_window
    )


def round_up_to_half(temperature: float) -> float:
    """The temperature rounded up to the next 0.5 C (Hive's step); a tiny guard
    keeps float noise from lifting an exact half degree."""
    return ceil(temperature * 2 - 1e-9) / 2


def value_in_force_at_end(row: ComfortDayRecord | None) -> float:
    """The comfort temperature in force at the end of a stored day: raised by a
    step if it was raised; a row from before the stepped rule (no quiet count)
    rounded up to a half degree, but never below the floor; the floor with no
    row."""
    if row is None or row.comfort_temp is None:
        return COMFORT_FLOOR
    if row.raised_at is not None:
        return row.comfort_temp + COMFORT_STEP
    if row.quiet_count is None:
        return max(COMFORT_FLOOR, round_up_to_half(row.comfort_temp))
    return row.comfort_temp


def start_of_next_day(row: ComfortDayRecord | None) -> tuple[float, int]:
    """The (comfort temperature, Quiet Days counted) a day starts from, given the
    stored day before it: a step down, never below the floor, once the count
    reaches QUIET_DAYS_PER_STEP."""
    value = value_in_force_at_end(row)
    count = 0 if row is None or row.quiet_count is None else row.quiet_count
    if row is not None and row.raised_at is not None:
        count = 0
    if count >= QUIET_DAYS_PER_STEP:
        return max(COMFORT_FLOOR, value - COMFORT_STEP), 0
    return value, count


@dataclass(frozen=True)
class DayOutcome:
    """What a day settles to: the comfort temperature it started at, when a Held
    Boost raised it, its kind ('held_boost', 'quiet' or None) and the Quiet Days
    counted at its end."""

    comfort_temp: float
    raised_at: datetime | None
    day_kind: DayKind
    quiet_count: int


def settle_day(
    previous: ComfortDayRecord | None,
    polls: Sequence[ThermostatPoll],
    window: tuple[datetime, datetime],
    no_one_home: list[Period],
) -> DayOutcome:
    """The day's outcome from the stored day before it, its polls (from one boost
    gap before midnight), its Comfort Window (naive UTC) and the no_one_home
    periods during it."""
    comfort_temp, quiet_count = start_of_next_day(previous)
    raised_at = held_boost_start(polls, comfort_temp, window)
    if raised_at is not None:
        return DayOutcome(comfort_temp, raised_at, "held_boost", 0)
    if is_quiet_day(polls, comfort_temp, window, no_one_home):
        return DayOutcome(comfort_temp, None, "quiet", quiet_count + 1)
    return DayOutcome(comfort_temp, None, None, quiet_count)


def counted_hours(heating_on_hours: Iterable[datetime]) -> list[int]:
    """The local hours of day, ascending, of the hours (naive UTC starts) in which
    the heating was on."""
    return sorted({local_day.to_local_hour(hour) for hour in heating_on_hours})


def minutes_below(
    polls: Iterable[ThermostatPoll],
    comfort_temp: float,
    hours: Iterable[int],
    no_one_home: list[Period],
    raised_at: datetime | None = None,
) -> int:
    """The minutes of the day's polls in which the room was more than the margin
    below the value in force (the comfort temperature, a step more from raised_at
    on) during a counted hour, leaving out offline polls and polls inside a
    no_one_home period. The margin keeps the thermostat's normal swing around its
    target from counting as discomfort."""
    counted = set(hours)
    return POLL_MINUTES * sum(
        _reachable(poll)
        and poll.current_temp is not None
        and poll.current_temp
        < comfort_temp
        - HELD_MARGIN
        + (COMFORT_STEP if raised_at is not None and poll.polled_at >= raised_at else 0)
        and local_day.to_local_hour(poll.polled_at) in counted
        and not _within(poll.polled_at, no_one_home)
        for poll in polls
    )

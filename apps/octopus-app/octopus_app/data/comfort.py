"""The comfort temperature: the room temperature at which the household usually
reaches for boost (ADR-0034)."""

from collections.abc import Iterable
from datetime import datetime, timedelta
from statistics import median

LEARNING_DAYS = 90
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


def comfort_temperature(
    start_temps: list[float], carried: float | None
) -> float | None:
    """The median room temperature at the boost starts; with under 5 of them, the
    last learned value carries on."""
    if len(start_temps) < MIN_BOOST_STARTS:
        return carried
    return median(start_temps)

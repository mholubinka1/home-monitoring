"""Hourly heating: which hours had enough thermostat polls, how long the heating
was on in each, and what that cost."""

from collections import defaultdict
from datetime import datetime

MIN_POLLS_PER_HOUR = 24  # of the 30 a 2-minute poll interval gives
POLL_MINUTES = 2
DEFAULT_BOILER_KW = 12.0
ESTIMATED = "estimated"

Poll = tuple[datetime, str | None, bool | None]


def heating_on_minutes(polls: list[Poll]) -> dict[datetime, tuple[int, int]]:
    """Per hour start, (poll count, minutes the heating was on), for hours with
    enough polls. A poll is "on" when the thermostat calls for heat or boost is
    active."""
    counts: dict[datetime, int] = defaultdict(int)
    on: dict[datetime, int] = defaultdict(int)
    for polled_at, state, boost_active in polls:
        hour = polled_at.replace(minute=0, second=0, microsecond=0)
        counts[hour] += 1
        on[hour] += state == "ON" or bool(boost_active)
    return {
        hour: (count, POLL_MINUTES * on[hour])
        for hour, count in counts.items()
        if count >= MIN_POLLS_PER_HOUR
    }


def estimated_cost(
    on_minutes: int, unit_rate: float | None
) -> tuple[float, float | None]:
    """The estimated kWh for the minutes on, and its cost in pounds (None
    without a rate in pence per kWh)."""
    kwh = on_minutes / 60 * DEFAULT_BOILER_KW
    return kwh, None if unit_rate is None else kwh * unit_rate / 100

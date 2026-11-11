"""Hourly heating: which hours had enough thermostat polls, how long the heating
was on in each, and what that cost."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime
from statistics import median

from common import local_day

MIN_POLLS_PER_HOUR = 24  # of the 30 a 2-minute poll interval gives
POLL_MINUTES = 2
DEFAULT_BOILER_KW = 12.0
ESTIMATED = "estimated"
MEASURED = "measured"
MIN_BASELINE_HOURS = 5  # zero-on hours of one hour of day before it has its own
BASELINE_DAYS = 28

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


def measured_gas(readings: Iterable[tuple[datetime, float]]) -> dict[datetime, float]:
    """Per hour start (naive UTC), the gas metered in the hour: the sum of its two
    half-hours, only when both exist and the readings for that London day total
    above zero (a day of zeros is the upstream "missing" marker)."""
    day_totals: dict[date, float] = defaultdict(float)
    halves: dict[datetime, list[float]] = defaultdict(list)
    for period_from, kwh in readings:
        day_totals[local_day.to_local_date(period_from)] += kwh
        halves[period_from.replace(minute=0, second=0, tzinfo=None)].append(kwh)
    return {
        hour: sum(kwhs)
        for hour, kwhs in halves.items()
        if len(kwhs) == 2 and day_totals[local_day.to_local_date(hour)] > 0
    }


def hot_water_baselines(
    zero_on_hours: Iterable[tuple[datetime, float]],
) -> dict[int, float]:
    """Per London hour of day, the median metered kWh of heating-off hours. An
    hour of day with under 5 such hours gets the median over all of them, and
    with none at all the baseline is 0."""
    by_hour: dict[int, list[float]] = defaultdict(list)
    for hour, kwh in zero_on_hours:
        by_hour[_london_hour_of_day(hour)].append(kwh)
    every = [kwh for kwhs in by_hour.values() for kwh in kwhs]
    overall = median(every) if every else 0.0
    return {
        hour_of_day: median(kwhs) if len(kwhs) >= MIN_BASELINE_HOURS else overall
        for hour_of_day, kwhs in by_hour.items()
    } | {
        -1: overall
    }  # -1: any hour of day with no heating-off hours


def baseline_for(baselines: dict[int, float], hour: datetime) -> float:
    return baselines.get(_london_hour_of_day(hour), baselines.get(-1, 0.0))


def measured_heating_kwh(measured_kwh: float, baseline: float) -> float:
    """The metered gas less the hot-water baseline, within 0 and the metered gas."""
    return min(measured_kwh, max(0.0, measured_kwh - baseline))


def _london_hour_of_day(hour: datetime) -> int:
    return hour.replace(tzinfo=UTC).astimezone(local_day.get_local_timezone()).hour

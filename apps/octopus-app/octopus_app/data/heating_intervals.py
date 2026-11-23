"""Actual heating in half-hours, the gas meter's grain: which half-hours had enough
thermostat polls, how long the heating was on in each, and what that cost."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import median

from common import local_day

INTERVAL_MINUTES = 30
INTERVAL = timedelta(minutes=INTERVAL_MINUTES)
POLL_MINUTES = 2
MIN_POLLS_PER_INTERVAL = 12  # of the 15 a 2-minute poll interval gives
DEFAULT_BOILER_KW = 12.0
ESTIMATED = "estimated"
MEASURED = "measured"
MIN_BASELINE_SAMPLES = 5  # heating-off half-hours of one half-hour of day
BASELINE_DAYS = 28
MIN_BOILER_KW_INTERVALS = 10  # measured half-hours before the boiler kW is learned
MIN_BOILER_KW_ON_MINUTES = 10
ANY_HALF_HOUR_OF_DAY = -1
WARM_UP = timedelta(minutes=20)
NO_UNIQUE_SOLUTION = 1e-9  # determinant below which the fit has no unique answer
MAX_GAP_IN_RUN = timedelta(minutes=5)  # longest gap between ON polls in one run
# How far before a window to read polls, so a Heating Run that began before it is
# seen from its first ON poll.
RUN_LOOKBACK = WARM_UP + MAX_GAP_IN_RUN

Poll = tuple[datetime, str | None, bool | None]


def poll_is_on(state: str | None, boost_active: bool | None) -> bool:
    """A poll is "on" when the thermostat calls for heat or boost is active."""
    return state == "ON" or bool(boost_active)


def interval_start_of(moment: datetime) -> datetime:
    """The start of the half-hour holding a moment (naive UTC)."""
    return moment.replace(minute=moment.minute // 30 * 30, second=0, microsecond=0)


@dataclass(frozen=True)
class BoilerPower:
    """The Warm-up kW (first 20 minutes of a Heating Run) and Steady kW (after)."""

    warm_up_kw: float
    steady_kw: float


DEFAULT_POWER = BoilerPower(DEFAULT_BOILER_KW, DEFAULT_BOILER_KW)


def heating_on_minutes(polls: list[Poll]) -> dict[datetime, tuple[int, int, int]]:
    """Per half-hour start, (poll count, minutes the heating was on, early on
    minutes), for half-hours with enough polls. Early minutes are those of ON polls
    under 20 minutes after their Heating Run's first ON poll; a run carries across
    half-hours. A retried poll can give a half-hour more polls than usual; the
    heating cannot have been on for longer than the half-hour."""
    counts: dict[datetime, int] = defaultdict(int)
    on: dict[datetime, int] = defaultdict(int)
    early: dict[datetime, int] = defaultdict(int)
    run_start: datetime | None = None
    last_on: datetime | None = None
    for polled_at, state, boost_active in sorted(polls, key=lambda poll: poll[0]):
        start = interval_start_of(polled_at)
        counts[start] += 1
        if not poll_is_on(state, boost_active):
            continue
        if run_start is None or last_on is None or polled_at - last_on > MAX_GAP_IN_RUN:
            run_start = polled_at
        last_on = polled_at
        on[start] += 1
        if polled_at - run_start < WARM_UP:
            early[start] += 1
    result = {}
    for start, count in counts.items():
        if count >= MIN_POLLS_PER_INTERVAL:
            on_minutes = min(INTERVAL_MINUTES, POLL_MINUTES * on[start])
            result[start] = (
                count,
                on_minutes,
                min(on_minutes, POLL_MINUTES * early[start]),
            )
    return result


def learned_boiler_power(
    measured_intervals: Iterable[tuple[int, int, float]],
) -> BoilerPower | None:
    """The Warm-up kW and Steady kW from measured (on minutes, early on minutes,
    heating kWh) half-hours with any minutes on, by least squares with no
    intercept; None under 10 such half-hours, with no unique solution, or when
    either figure is not above zero."""
    rows = [
        (early / 60, (on_minutes - early) / 60, kwh)
        for on_minutes, early, kwh in measured_intervals
        if on_minutes > 0
    ]
    if len(rows) < MIN_BOILER_KW_INTERVALS:
        return None
    aa = sum(a * a for a, _, _ in rows)
    ab = sum(a * b for a, b, _ in rows)
    bb = sum(b * b for _, b, _ in rows)
    ay = sum(a * y for a, _, y in rows)
    by = sum(b * y for _, b, y in rows)
    det = aa * bb - ab * ab
    if abs(det) < NO_UNIQUE_SOLUTION:
        return None
    power = BoilerPower((ay * bb - by * ab) / det, (by * aa - ay * ab) / det)
    return power if power.warm_up_kw > 0 and power.steady_kw > 0 else None


def learned_boiler_kw(measured_intervals: Iterable[tuple[int, float]]) -> float:
    """The kW the boiler drew while on, from measured (on minutes, heating kWh)
    half-hours with at least 10 minutes on; the default under 10 such half-hours,
    or when they come to no gas at all (a baseline above what was metered)."""
    usable = [
        (on_minutes, kwh)
        for on_minutes, kwh in measured_intervals
        if on_minutes >= MIN_BOILER_KW_ON_MINUTES
    ]
    if len(usable) < MIN_BOILER_KW_INTERVALS:
        return DEFAULT_BOILER_KW
    learned = sum(kwh for _, kwh in usable) / sum(m / 60 for m, _ in usable)
    return learned if learned > 0 else DEFAULT_BOILER_KW


def estimated_cost(
    on_minutes: float,
    early_on_minutes: float,
    unit_rate: float | None,
    power: BoilerPower,
) -> tuple[float, float | None]:
    """The estimated kWh for the minutes on, the early ones at the Warm-up kW and
    the rest at the Steady kW, and its cost in pounds (None without a rate in
    pence per kWh)."""
    kwh = (
        early_on_minutes * power.warm_up_kw
        + (on_minutes - early_on_minutes) * power.steady_kw
    ) / 60
    return kwh, None if unit_rate is None else kwh * unit_rate / 100


def measured_gas(readings: Iterable[tuple[datetime, float]]) -> dict[datetime, float]:
    """Per half-hour start (naive UTC), the gas metered in it, only when the
    readings for that London day total above zero (a day of zeros is the upstream
    "missing" marker)."""
    day_totals: dict[date, float] = defaultdict(float)
    per_interval: dict[datetime, float] = {}
    for period_from, kwh in readings:
        day_totals[local_day.to_local_date(period_from)] += kwh
        per_interval[period_from.replace(tzinfo=None)] = kwh
    return {
        start: kwh
        for start, kwh in per_interval.items()
        if day_totals[local_day.to_local_date(start)] > 0
    }


def local_half_hour_of_day(instant: datetime) -> int:
    """The local half-hour of day (0 to 47) of an instant; a naive one is UTC."""
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    local = instant.astimezone(local_day.get_local_timezone())
    return local.hour * 2 + local.minute // 30


def hot_water_baselines(
    zero_on_intervals: Iterable[tuple[datetime, float]],
) -> dict[int, float]:
    """Per London half-hour of day, the median metered kWh of heating-off
    half-hours. A half-hour of day with under 5 such samples gets the median over
    all of them, and with none at all the baseline is 0."""
    by_slot: dict[int, list[float]] = defaultdict(list)
    for start, kwh in zero_on_intervals:
        by_slot[local_half_hour_of_day(start)].append(kwh)
    every = [kwh for kwhs in by_slot.values() for kwh in kwhs]
    overall = median(every) if every else 0.0
    return {
        slot: median(kwhs) if len(kwhs) >= MIN_BASELINE_SAMPLES else overall
        for slot, kwhs in by_slot.items()
    } | {ANY_HALF_HOUR_OF_DAY: overall}


def baseline_for(baselines: dict[int, float], start: datetime) -> float:
    return baselines.get(
        local_half_hour_of_day(start), baselines.get(ANY_HALF_HOUR_OF_DAY, 0.0)
    )


def measured_heating_kwh(measured_kwh: float, baseline: float) -> float:
    """The metered gas less the hot-water baseline, within 0 and the metered gas."""
    return min(measured_kwh, max(0.0, measured_kwh - baseline))

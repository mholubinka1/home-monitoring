"""Pure heating-model calculations: no database access."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from octopus_app.data.gas_day import is_usable
from octopus_app.data.model import ConsumptionSummary, Energy

# Model parameters, all in one place.
BASELOAD_WINDOW_DAYS = 120  # trailing window (ending on the day) for the baseload
MIN_WARM_DAYS = 8  # warm days a window needs for a measured baseload
WARM_MARGIN = 1.0  # a warm day is at least this far above the threshold (C)
FIT_WINDOW_DAYS = 365  # days ending as_of that the fit uses
THRESHOLDS = tuple(round(8.0 + 0.1 * i, 1) for i in range(101))  # 8.0..18.0
WEIGHT_MAX = 0.8  # the blend weight of yesterday never exceeds this
WEIGHTS = tuple(round(0.05 * i, 2) for i in range(round(WEIGHT_MAX / 0.05) + 1))
AWAY_FRACTION = 1 / 3  # a day under this share of the baseload is an away day
MIN_USABLE_DAYS = 150  # fewer usable days than this: the fit is unusable
MIN_DAYS_EACH_SIDE = 30  # days needed below and at/above the threshold
MIN_R_SQUARED = 0.5


def effective_temperature(
    todays_mean: float | None, yesterdays_mean: float | None, weight: float
) -> float | None:
    """Today's mean blended with yesterday's: the house's slow thermal response
    means yesterday's weather still affects today's heating. A missing or
    non-finite mean on either day gives no effective temperature."""
    if not (
        todays_mean is not None
        and yesterdays_mean is not None
        and math.isfinite(todays_mean)
        and math.isfinite(yesterdays_mean)
    ):
        return None
    return (1 - weight) * todays_mean + weight * yesterdays_mean


def effective_temperatures(
    daily_means: Mapping[date, float], weight: float
) -> dict[date, float]:
    """The effective temperature of every day whose own mean and previous
    calendar day's mean both exist."""
    effective = {
        day: effective_temperature(
            mean, daily_means.get(day - timedelta(days=1)), weight
        )
        for day, mean in daily_means.items()
    }
    return {day: value for day, value in effective.items() if value is not None}


class BaselineKind(Enum):
    MEASURED = "measured"
    INTERPOLATED = "interpolated"
    HELD = "held"


@dataclass(frozen=True)
class BaselinePoint:
    kwh: float
    kind: BaselineKind


def _fill_baseload(measured: list[float | None]) -> list[tuple[float, BaselineKind]]:
    """Measured values stay; gaps are interpolated on a straight line between the
    nearest measured days either side, or held at the nearest measured value when
    only one side has one. Returns [] when nothing was measured."""
    known = [(i, value) for i, value in enumerate(measured) if value is not None]
    if not known:
        return []
    filled: list[tuple[float, BaselineKind]] = []
    after = 0  # index into `known` of the first measured day at or after i
    for i, value in enumerate(measured):
        if value is not None:
            filled.append((value, BaselineKind.MEASURED))
            after += 1
        elif after == 0:
            filled.append((known[0][1], BaselineKind.HELD))
        elif after == len(known):
            filled.append((known[-1][1], BaselineKind.HELD))
        else:
            (lo, lo_value), (hi, hi_value) = known[after - 1], known[after]
            fraction = (i - lo) / (hi - lo)
            filled.append(
                (lo_value + fraction * (hi_value - lo_value), BaselineKind.INTERPOLATED)
            )
    return filled


def _baseload_series(
    gas: Sequence[float | None], effective: Sequence[float | None], threshold: float
) -> list[tuple[float, BaselineKind]]:
    """The moving baseload of each calendar position, given gas and effective
    temperature lists (None where missing or excluded)."""
    warm_from = threshold + WARM_MARGIN
    counts = [0]
    sums = [0.0]
    for g, t in zip(gas, effective, strict=True):
        if g is not None and t is not None and t >= warm_from:
            counts.append(counts[-1] + 1)
            sums.append(sums[-1] + g)
        else:
            counts.append(counts[-1])
            sums.append(sums[-1])
    measured: list[float | None] = []
    for i in range(len(gas)):
        lo = max(0, i + 1 - BASELOAD_WINDOW_DAYS)
        warm_days = counts[i + 1] - counts[lo]
        measured.append(
            (sums[i + 1] - sums[lo]) / warm_days if warm_days >= MIN_WARM_DAYS else None
        )
    return _fill_baseload(measured)


def _calendar_span(gas: Mapping[date, float]) -> tuple[date, int]:
    """The first date and the number of calendar days from the earliest to the
    latest gas date: the one definition of the series' index-to-date mapping."""
    start = min(gas)
    return start, (max(gas) - start).days + 1


def _calendar(
    mapping: Mapping[date, float], start: date, length: int
) -> list[float | None]:
    return [mapping.get(start + timedelta(days=i)) for i in range(length)]


def moving_baseload(
    gas: Mapping[date, float],
    effective: Mapping[date, float],
    threshold: float,
) -> dict[date, BaselinePoint]:
    """The non-heating gas use of every calendar day from the earliest to the
    latest gas date: the mean gas of the warm days in the trailing window.

    `gas` must already hold only usable days (see gas_day.is_usable): a zero
    total is missing data, and passed in here it would count as a warm day and
    pull the baseload down."""
    if not gas:
        return {}
    start, length = _calendar_span(gas)
    series = _baseload_series(
        _calendar(gas, start, length), _calendar(effective, start, length), threshold
    )
    return {
        start + timedelta(days=i): BaselinePoint(kwh, kind)
        for i, (kwh, kind) in enumerate(series)
    }


class UnusableReason(Enum):
    TOO_FEW_DAYS = "too few usable days"
    TOO_FEW_DAYS_ON_ONE_SIDE = "too few days on one side of the threshold"
    POOR_FIT = "the fit explains too little of the variation"
    THRESHOLD_AT_EDGE = "the threshold is at the edge of the search range"
    NON_POSITIVE_SLOPE = "gas use does not rise as it gets colder"
    NO_FIT = (
        "no fit could be scored: no days below any threshold, "
        "or no baseload could be measured"
    )


@dataclass(frozen=True)
class FitQuality:
    r_squared: float
    days_used: int
    days_below_threshold: int
    days_above_threshold: int


@dataclass(frozen=True)
class HeatingFit:
    usable: bool
    reason: UnusableReason | None
    # threshold, slope, weight and quality are None, and baseload is empty,
    # exactly when no fit could be made; a fit that fails a guard keeps its real
    # numbers and baseload.
    threshold: float | None
    slope: float | None
    weight: float | None
    quality: FitQuality | None
    away_days: frozenset[date]
    baseload: dict[date, BaselinePoint]


@dataclass(frozen=True)
class _Candidate:
    error: float
    threshold: float
    weight: float
    slope: float
    baseload: list[tuple[float, BaselineKind]]


_Day = tuple[int, float, float]  # calendar index, gas, effective temperature


def _fit_window_days(
    gas: Sequence[float | None], effective: Sequence[float | None], window_start: int
) -> list[_Day]:
    return [
        (i, g, t)
        for i in range(window_start, len(gas))
        if (g := gas[i]) is not None and (t := effective[i]) is not None
    ]


def _score(
    days: Sequence[_Day], series: Sequence[tuple[float, BaselineKind]], threshold: float
) -> tuple[float, float]:
    """(error, slope) of the best slope for one threshold; the error is infinite
    when no day is below it. The best slope has a closed form, so this is one pass."""
    sum_xx = sum_xe = sum_ee = 0.0
    for i, g, t in days:
        x = max(0.0, threshold - t)
        excess = g - series[i][0]
        sum_xx += x * x
        sum_xe += x * excess
        sum_ee += excess * excess
    if sum_xx == 0:
        return math.inf, 0.0
    slope = sum_xe / sum_xx
    return sum_ee - slope * sum_xe, slope


def _best_candidate(
    gas: Sequence[float | None],
    effective_by_weight: Mapping[float, Sequence[float | None]],
    thresholds: Sequence[float],
    window_start: int,
) -> _Candidate | None:
    """The (threshold, weight) with the smallest squared error over the fit
    window days, or None when no combination could be scored: either no day is
    below any threshold, or no baseload could be measured for any combination
    (e.g. a cold-only history where no window reaches MIN_WARM_DAYS warm days)."""
    best: _Candidate | None = None
    for weight, effective in effective_by_weight.items():
        days = _fit_window_days(gas, effective, window_start)
        for threshold in thresholds:
            series = _baseload_series(gas, effective, threshold)
            error, slope = (
                _score(days, series, threshold) if series else (math.inf, 0.0)
            )
            if error < (best.error if best else math.inf):
                best = _Candidate(error, threshold, weight, slope, series)
    return best


def _unusable(reason: UnusableReason, away_days: frozenset[date]) -> HeatingFit:
    return HeatingFit(
        usable=False,
        reason=reason,
        threshold=None,
        slope=None,
        weight=None,
        quality=None,
        away_days=away_days,
        baseload={},
    )


def _quality(candidate: _Candidate, days: Sequence[_Day]) -> FitQuality:
    mean = sum(g for _, g, _ in days) / len(days)
    total = sum((g - mean) ** 2 for _, g, _ in days)
    residual = sum(
        (
            g
            - candidate.baseload[i][0]
            - candidate.slope * max(0.0, candidate.threshold - t)
        )
        ** 2
        for i, g, t in days
    )
    below = sum(t < candidate.threshold for _, _, t in days)
    return FitQuality(
        r_squared=1 - residual / total if total > 0 else 0.0,
        days_used=len(days),
        days_below_threshold=below,
        days_above_threshold=len(days) - below,
    )


def _failed_guard(
    candidate: _Candidate, quality: FitQuality, thresholds: Sequence[float]
) -> UnusableReason | None:
    if quality.days_used < MIN_USABLE_DAYS:
        return UnusableReason.TOO_FEW_DAYS
    if (
        min(quality.days_below_threshold, quality.days_above_threshold)
        < MIN_DAYS_EACH_SIDE
    ):
        return UnusableReason.TOO_FEW_DAYS_ON_ONE_SIDE
    if quality.r_squared < MIN_R_SQUARED:
        return UnusableReason.POOR_FIT
    if candidate.threshold in (min(thresholds), max(thresholds)):
        return UnusableReason.THRESHOLD_AT_EDGE
    if candidate.slope <= 0:
        return UnusableReason.NON_POSITIVE_SLOPE
    return None


def _away_indices(gas: Sequence[float | None], candidate: _Candidate) -> set[int]:
    return {
        i
        for i, g in enumerate(gas)
        if g is not None and g < candidate.baseload[i][0] * AWAY_FRACTION
    }


def _calendar_inputs(
    gas: Mapping[date, float],
    daily_means: Mapping[date, float],
    weights: Sequence[float],
) -> tuple[list[float | None], dict[float, list[float | None]]]:
    start, length = _calendar_span(gas)
    return _calendar(gas, start, length), {
        weight: _calendar(effective_temperatures(daily_means, weight), start, length)
        for weight in weights
    }


@dataclass(frozen=True)
class _Problem:
    start: date
    gas: list[float | None]
    effective_by_weight: dict[float, list[float | None]]
    thresholds: Sequence[float]
    window_start: int

    def best(self, gas: Sequence[float | None]) -> _Candidate | None:
        return _best_candidate(
            gas, self.effective_by_weight, self.thresholds, self.window_start
        )


def _refit_without_away_days(problem: _Problem, first: _Candidate) -> HeatingFit:
    """Mark away days from the first fit, drop them and fit a second time."""
    away = _away_indices(problem.gas, first)
    away_days = frozenset(problem.start + timedelta(days=i) for i in away)
    kept = [None if i in away else g for i, g in enumerate(problem.gas)]
    final = problem.best(kept)
    if final is None:
        return _unusable(UnusableReason.NO_FIT, away_days)
    used = _fit_window_days(
        kept, problem.effective_by_weight[final.weight], problem.window_start
    )
    quality = _quality(final, used)
    reason = _failed_guard(final, quality, problem.thresholds)
    return HeatingFit(
        usable=reason is None,
        reason=reason,
        threshold=final.threshold,
        slope=final.slope,
        weight=final.weight,
        quality=quality,
        away_days=away_days,
        baseload={
            problem.start + timedelta(days=i): BaselinePoint(kwh, kind)
            for i, (kwh, kind) in enumerate(final.baseload)
        },
    )


def fit_heating_model(
    summaries: Sequence[ConsumptionSummary],
    daily_means: Mapping[date, float],
    as_of: date,
    *,
    thresholds: Sequence[float] = THRESHOLDS,
    weights: Sequence[float] = WEIGHTS,
) -> HeatingFit:
    """Fit gas use = moving baseload + slope * degree-days below a threshold of
    the blended effective temperature, over the FIT_WINDOW_DAYS ending as_of.

    Days under AWAY_FRACTION of their baseload are away days: they are dropped
    from the baseload and the fit, and the model is fitted a second time. They
    are looked for across all the history supplied, not only the fit window, so
    `away_days` can hold dates before the window.

    The window ends at `as_of`, not at the newest gas day: a caller should pass
    the latest complete gas day, or the window holds fewer than
    FIT_WINDOW_DAYS days of data.

    Only gas summaries are used; other energies are ignored.

    Raises ValueError when a weight is outside 0..WEIGHT_MAX."""
    if not all(0 <= weight <= WEIGHT_MAX for weight in weights):
        raise ValueError(f"weights must each be between 0 and {WEIGHT_MAX}")
    gas = {
        s.date: float(s.total_kwh)
        for s in summaries
        if s.energy is Energy.gas and is_usable(s) and s.date <= as_of
    }
    if not gas:
        return _unusable(UnusableReason.TOO_FEW_DAYS, frozenset())
    start, _ = _calendar_span(gas)
    gas_list, effective_by_weight = _calendar_inputs(gas, daily_means, weights)
    problem = _Problem(
        start=start,
        gas=gas_list,
        effective_by_weight=effective_by_weight,
        thresholds=thresholds,
        window_start=max(0, (as_of - timedelta(days=FIT_WINDOW_DAYS - 1) - start).days),
    )
    first = problem.best(gas_list)
    if first is not None:
        return _refit_without_away_days(problem, first)
    # Count the window days the fit could use: gas AND an effective temperature
    # (which exists for every weight alike, so any weight's list will do).
    temperatures = list(effective_by_weight.values())
    usable_days = sum(
        gas_list[i] is not None and any(t[i] is not None for t in temperatures)
        for i in range(problem.window_start, len(gas_list))
    )
    too_few = usable_days < MIN_USABLE_DAYS
    reason = UnusableReason.TOO_FEW_DAYS if too_few else UnusableReason.NO_FIT
    return _unusable(reason, frozenset())

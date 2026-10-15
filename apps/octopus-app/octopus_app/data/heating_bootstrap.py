"""Pure bootstrap of the heating threshold's uncertainty: no database access."""

import math
import random
from collections.abc import Sequence

from octopus_app.data.heating_model import THRESHOLDS

# Model parameters, all in one place.
BOOTSTRAP_SAMPLES = 300  # resampled histories, each refitted for its threshold
BOOTSTRAP_UPPER_PERCENTILE = 0.95  # the upper end of the threshold's range
BOOTSTRAP_SEED = 20261015  # fixed so a refit is reproducible

_Day = tuple[float, float]  # effective temperature, gas above that day's baseload


def _best_threshold(days: Sequence[_Day], thresholds: Sequence[float]) -> float | None:
    """The threshold with the smallest squared error (the lowest on a tie), or
    None when no threshold has a day below it. For one threshold the best slope
    has a closed form, so each threshold is one pass over the days."""
    best: float | None = None
    best_error = math.inf
    for threshold in thresholds:
        sum_xx = sum_xe = sum_ee = 0.0
        for temperature, excess in days:
            x = threshold - temperature
            if x > 0:
                sum_xx += x * x
                sum_xe += x * excess
            sum_ee += excess * excess
        if sum_xx == 0:
            continue
        error = sum_ee - sum_xe * sum_xe / sum_xx
        if error < best_error:
            best, best_error = threshold, error
    return best


def bootstrap_threshold_upper(
    days: Sequence[_Day],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
    thresholds: Sequence[float] = THRESHOLDS,
) -> float | None:
    """The upper end of the heating threshold's uncertainty: refit the
    threshold on `samples` histories resampled with replacement from `days`
    ((effective temperature, gas above that day's baseload) pairs) and return
    the BOOTSTRAP_UPPER_PERCENTILE of the refitted thresholds, taken as the
    sorted value at index ceil(0.95 * n) - 1. The same days and seed always give
    the same answer.

    A resample where no day is below any threshold cannot be scored and is
    skipped. Returns None when there are fewer than 2 days or no resample could
    be scored.

    Raises ValueError when samples is not positive or a day holds a non-finite
    value."""
    if samples <= 0:
        raise ValueError(f"samples must be above zero, not {samples}")
    if not all(math.isfinite(value) for day in days for value in day):
        raise ValueError("days must be finite")
    if len(days) < 2:
        return None
    # A resample needs to be reproducible, not unpredictable.
    rng = random.Random(seed)  # nosec B311 -- not a security use of randomness
    best = sorted(
        threshold
        for _ in range(samples)
        if (threshold := _best_threshold(rng.choices(days, k=len(days)), thresholds))
        is not None
    )
    if not best:
        return None
    return best[math.ceil(BOOTSTRAP_UPPER_PERCENTILE * len(best)) - 1]

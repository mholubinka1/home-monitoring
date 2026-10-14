"""Pure verdicts on whether a day's gas was needed: no database access."""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

# Model parameters, all in one place.
NOISE_FLOOR_KWH = 1.0  # a day is never judged on a margin tighter than this (kWh)
# For normal noise the mean absolute residual is sigma * sqrt(2 / pi), so
# scaling by its inverse turns fitted mean sizes into standard deviations.
MEAN_ABS_TO_SIGMA = math.sqrt(math.pi / 2)
# Spread in degree-days (sum of squared deviations) below this is float rounding
# noise on repeated values, not real variation to fit a slope to.
MIN_DEGREE_DAY_SPREAD = 1e-9


def _require_finite(names: str, *values: float) -> None:
    """Raise ValueError naming `names` unless every value is a finite number."""
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{names} must be finite")


@dataclass(frozen=True)
class NoiseModel:
    """How far a day's gas normally strays from its expected value."""

    base: float
    per_degree_day: float
    floor: float

    def __post_init__(self) -> None:
        _require_finite(
            "noise base, per_degree_day and floor",
            self.base,
            self.per_degree_day,
            self.floor,
        )
        if self.floor <= 0:
            raise ValueError(f"noise floor must be above zero, not {self.floor}")

    def margin(self, degree_days: float) -> float:
        """The normal variation (kWh) on a day needing this many degree-days,
        never below the floor."""
        return max(self.floor, self.base + self.per_degree_day * degree_days)


class DayStatus(Enum):
    NEEDED = "needed"
    NOT_NEEDED = "not_needed"
    AWAY = "away"


@dataclass(frozen=True)
class DayVerdict:
    status: DayStatus
    expected_gas: float
    margin: float
    baseload: float
    expected_heating: float
    normal_variation: float
    possible: float
    clear: float


@dataclass(frozen=True)
class JudgingModel:
    """What every day is judged against: the fitted threshold and slope, the
    upper end of the threshold's uncertainty range (never below the threshold),
    and the noise. A usable fit always has a positive slope."""

    threshold: float
    slope: float
    threshold_upper: float
    noise: NoiseModel

    def __post_init__(self) -> None:
        _require_finite(
            "threshold, slope and threshold_upper",
            self.threshold,
            self.slope,
            self.threshold_upper,
        )
        if self.slope <= 0:
            raise ValueError(f"slope must be above zero, not {self.slope}")
        if self.threshold_upper < self.threshold:
            raise ValueError(
                f"threshold_upper {self.threshold_upper} is below "
                f"threshold {self.threshold}"
            )


def judge_day(
    *,
    gas: float,
    effective: float,
    baseload: float,
    model: JudgingModel,
    away: bool = False,
) -> DayVerdict:
    """Split a day's gas into slices that sum to it (to within float rounding).

    Raises ValueError when gas, effective temperature or baseload is not a
    finite number: a NaN would break the sum without any other sign."""
    _require_finite("gas, effective temperature and baseload", gas, effective, baseload)
    degree_days = max(0.0, model.threshold - effective)
    expected_heating = model.slope * degree_days
    expected_gas = baseload + expected_heating
    margin = model.noise.margin(degree_days)
    residual = gas - expected_gas
    if away:
        # Nothing to blame: the whole residual is normal variation.
        status = DayStatus.AWAY
        normal_variation, possible, clear = residual, 0.0, 0.0
    else:
        status = (
            DayStatus.NEEDED
            if effective < model.threshold_upper
            else DayStatus.NOT_NEEDED
        )
        normal_variation = min(residual, margin)
        possible = min(max(residual - margin, 0.0), margin)
        clear = max(0.0, residual - 2 * margin)
    return DayVerdict(
        status=status,
        expected_gas=expected_gas,
        margin=margin,
        baseload=baseload,
        expected_heating=expected_heating,
        normal_variation=normal_variation,
        possible=possible,
        clear=clear,
    )


def setpoint_saving(slope: float, verdicts: Iterable[DayVerdict]) -> float:
    """Gas (kWh) saved over the period by a 1 C lower setpoint: the slope for
    each needed day. Away and not-needed days use no heating to save."""
    return slope * sum(1 for v in verdicts if v.status is DayStatus.NEEDED)


def fit_noise_model(
    residuals: Sequence[tuple[float, float]], floor: float = NOISE_FLOOR_KWH
) -> NoiseModel:
    """Fit how the size of daily residuals grows with degree-days, from
    (degree_days, residual) pairs, as a standard deviation. With fewer than two
    points or no spread in degree-days the noise is flat at the mean size; with
    no points the base is zero and the floor decides. A fit that falls with the
    cold is kept as fitted (the floor still bounds the margin).

    Raises ValueError when a degree-day or residual is not a finite number."""
    _require_finite(
        "degree-days and residuals", *(value for pair in residuals for value in pair)
    )
    if not residuals:
        return NoiseModel(base=0.0, per_degree_day=0.0, floor=floor)
    count = len(residuals)
    mean_dd = sum(dd for dd, _ in residuals) / count
    mean_size = sum(abs(r) for _, r in residuals) / count
    spread = sum((dd - mean_dd) ** 2 for dd, _ in residuals)
    if spread < MIN_DEGREE_DAY_SPREAD:
        return NoiseModel(
            base=mean_size * MEAN_ABS_TO_SIGMA, per_degree_day=0.0, floor=floor
        )
    slope = sum((dd - mean_dd) * (abs(r) - mean_size) for dd, r in residuals) / spread
    return NoiseModel(
        base=(mean_size - slope * mean_dd) * MEAN_ABS_TO_SIGMA,
        per_degree_day=slope * MEAN_ABS_TO_SIGMA,
        floor=floor,
    )

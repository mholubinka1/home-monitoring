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


@dataclass(frozen=True)
class NoiseModel:
    """How far a day's gas normally strays from its expected value."""

    base: float
    per_degree_day: float
    floor: float

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
    and the noise."""

    threshold: float
    slope: float
    threshold_upper: float
    noise: NoiseModel

    def __post_init__(self) -> None:
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
    """Split a day's gas into slices that add up exactly to it.

    Raises ValueError when gas, effective temperature or baseload is not a
    finite number: a NaN would break the sum without any other sign."""
    if not all(math.isfinite(value) for value in (gas, effective, baseload)):
        raise ValueError("gas, effective temperature and baseload must be finite")
    degree_days = max(0.0, model.threshold - effective)
    expected_heating = model.slope * degree_days
    expected_gas = baseload + expected_heating
    margin = model.noise.margin(degree_days)
    residual = gas - expected_gas
    if away:
        # Nothing to blame: the whole residual is normal variation.
        return DayVerdict(
            status=DayStatus.AWAY,
            expected_gas=expected_gas,
            margin=margin,
            baseload=baseload,
            expected_heating=expected_heating,
            normal_variation=residual,
            possible=0.0,
            clear=0.0,
        )
    return DayVerdict(
        status=(
            DayStatus.NEEDED
            if effective < model.threshold_upper
            else DayStatus.NOT_NEEDED
        ),
        expected_gas=expected_gas,
        margin=margin,
        baseload=baseload,
        expected_heating=expected_heating,
        normal_variation=min(residual, margin),
        possible=min(max(residual - margin, 0.0), margin),
        clear=max(0.0, residual - 2 * margin),
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
    points or no spread in degree-days the noise is flat at the mean size."""
    if not residuals:
        return NoiseModel(base=0.0, per_degree_day=0.0, floor=floor)
    count = len(residuals)
    mean_dd = sum(dd for dd, _ in residuals) / count
    mean_size = sum(abs(r) for _, r in residuals) / count
    spread = sum((dd - mean_dd) ** 2 for dd, _ in residuals)
    if spread == 0:
        return NoiseModel(
            base=mean_size * MEAN_ABS_TO_SIGMA, per_degree_day=0.0, floor=floor
        )
    slope = sum((dd - mean_dd) * (abs(r) - mean_size) for dd, r in residuals) / spread
    return NoiseModel(
        base=(mean_size - slope * mean_dd) * MEAN_ABS_TO_SIGMA,
        per_degree_day=slope * MEAN_ABS_TO_SIGMA,
        floor=floor,
    )

import math

import pytest

from octopus_app.data.heating_verdict import (
    NOISE_FLOOR_KWH,
    DayStatus,
    DayVerdict,
    JudgingModel,
    NoiseModel,
    fit_noise_model,
    judge_day,
    setpoint_saving,
)

THRESHOLD = 12.0
THRESHOLD_UPPER = 13.0
SLOPE = 3.0
BASELOAD = 7.0
# Margin of 2 kWh/day, whatever the heating need.
FLAT_NOISE = NoiseModel(base=2.0, per_degree_day=0.0, floor=0.5)
MODEL = JudgingModel(
    threshold=THRESHOLD, slope=SLOPE, threshold_upper=THRESHOLD_UPPER, noise=FLAT_NOISE
)


def _judge(gas: float, effective: float) -> DayVerdict:
    return judge_day(
        gas=gas,
        effective=effective,
        baseload=BASELOAD,
        model=MODEL,
    )


def _slices_total(verdict: DayVerdict) -> float:
    return (
        verdict.baseload
        + verdict.expected_heating
        + verdict.normal_variation
        + verdict.possible
        + verdict.clear
    )


@pytest.mark.parametrize(
    ("gas", "effective"),
    [
        (7.0, 20.0),  # warm day, exactly baseload
        (30.0, 5.0),  # cold day, far over expected
        (10.0, 5.0),  # cold day, far under expected
        (9.5, 11.0),  # cool day, slightly over
        (12.3, 12.5),  # between threshold and its upper end
        (0.5, 20.0),  # warm day, far under baseload
    ],
)
def test_a_days_slices_always_add_up_to_its_actual_gas(
    gas: float, effective: float
) -> None:
    verdict = _judge(gas, effective)

    assert _slices_total(verdict) == pytest.approx(gas)


def test_an_upper_threshold_below_the_threshold_is_rejected() -> None:
    with pytest.raises(ValueError, match="threshold_upper"):
        JudgingModel(
            threshold=THRESHOLD, slope=SLOPE, threshold_upper=11.0, noise=FLAT_NOISE
        )


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_a_day_with_a_non_finite_reading_is_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        _judge(gas=bad, effective=5.0)
    with pytest.raises(ValueError, match="finite"):
        _judge(gas=20.0, effective=bad)
    with pytest.raises(ValueError, match="finite"):
        judge_day(gas=20.0, effective=5.0, baseload=bad, model=MODEL)


def test_only_the_part_of_an_overshoot_beyond_one_margin_is_possible() -> None:
    # Cold day: expected gas is 7 + 3 * 7 = 28 kWh. 1.4 margins over is 30.8.
    verdict = _judge(gas=28.0 + 1.4 * 2.0, effective=5.0)

    assert verdict.normal_variation == pytest.approx(2.0)
    assert verdict.possible == pytest.approx(0.8)
    assert verdict.clear == 0.0


def test_a_day_inside_the_thresholds_uncertainty_range_is_judged_needed() -> None:
    # 12.5 C is above the fitted threshold (12) but below its upper end (13).
    verdict = _judge(gas=BASELOAD + 3 * FLAT_NOISE.base, effective=12.5)

    assert verdict.status is DayStatus.NEEDED


def test_an_overshoot_on_a_clearly_warm_day_is_avoidable() -> None:
    # 14 C is at or above the upper end: no heating was needed, so gas beyond
    # baseload plus the margin is avoidable.
    verdict = _judge(gas=BASELOAD + 2 * 2.0 + 1.0, effective=14.0)

    assert verdict.status is DayStatus.NOT_NEEDED
    assert verdict.possible > 0


def test_an_away_day_is_neutral() -> None:
    verdict = judge_day(
        gas=30.0,
        effective=5.0,
        baseload=BASELOAD,
        model=MODEL,
        away=True,
    )

    assert verdict.status is DayStatus.AWAY
    assert verdict.possible == 0.0
    assert verdict.clear == 0.0
    assert _slices_total(verdict) == pytest.approx(30.0)


def test_a_lower_setpoint_saves_the_slope_for_each_needed_day() -> None:
    away = judge_day(
        gas=0.0,
        effective=5.0,
        baseload=BASELOAD,
        model=MODEL,
        away=True,
    )
    verdicts = [
        _judge(gas=20.0, effective=8.0),  # needed
        _judge(gas=25.0, effective=5.0),  # needed
        _judge(gas=8.0, effective=16.0),  # not needed
        away,
    ]

    assert setpoint_saving(SLOPE, verdicts) == pytest.approx(2 * SLOPE)


# For normal noise the mean absolute residual is sigma * sqrt(2 / pi).
MEAN_ABS_PER_SIGMA = math.sqrt(2 / math.pi)


def _planted_residuals(base: float, per_degree_day: float) -> list[tuple[float, float]]:
    # Alternating signs; the size of each residual follows the planted noise.
    return [
        (dd, (-1) ** dd * MEAN_ABS_PER_SIGMA * (base + per_degree_day * dd))
        for dd in range(15)
    ]


def test_the_noise_model_recovers_noise_that_grows_with_degree_days() -> None:
    noise = fit_noise_model(_planted_residuals(base=1.5, per_degree_day=0.4))

    assert noise.base == pytest.approx(1.5)
    assert noise.per_degree_day == pytest.approx(0.4)


def test_the_noise_margin_never_drops_below_the_floor() -> None:
    noise = fit_noise_model(_planted_residuals(base=0.1, per_degree_day=0.0), floor=0.7)

    assert noise.margin(0.0) == 0.7


def test_a_falling_fit_cannot_shrink_the_margin_below_the_floor() -> None:
    noise = fit_noise_model(_planted_residuals(base=3.0, per_degree_day=-0.2))

    assert noise.margin(30.0) == noise.floor


def test_noise_without_spread_in_degree_days_uses_the_mean_residual() -> None:
    noise = fit_noise_model([(5.0, 2.0), (5.0, -4.0)])

    assert noise.base == pytest.approx(3.0 / MEAN_ABS_PER_SIGMA)
    assert noise.per_degree_day == 0.0


def test_a_single_residual_is_used_as_the_base_noise() -> None:
    noise = fit_noise_model([(4.0, -2.0)])

    assert noise.base == pytest.approx(2.0 / MEAN_ABS_PER_SIGMA)
    assert noise.per_degree_day == 0.0


def test_no_residuals_leave_the_floor_to_decide_the_margin() -> None:
    noise = fit_noise_model([])

    assert noise.margin(10.0) == NOISE_FLOOR_KWH

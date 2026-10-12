import math
import random
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest

from common import local_day
from octopus_app.data.heating_model import (
    THRESHOLDS,
    WEIGHT_MAX,
    WEIGHTS,
    BaselineKind,
    BaselinePoint,
    HeatingFit,
    UnusableReason,
    effective_temperature,
    fit_heating_model,
    moving_baseload,
)
from octopus_app.data.model import ConsumptionSummary, Energy

THRESHOLD = 12.0


def _days(start: date, count: int) -> list[date]:
    return [start + timedelta(days=d) for d in range(count)]


def test_a_steady_warm_baseload_is_measured_and_covers_every_day() -> None:
    days = [date(2026, 5, 1) + timedelta(days=d) for d in range(150)]
    gas = {day: 7.0 for day in days}
    # Every day is well above the threshold + 1 C, so every day is a warm day.
    effective = {day: 20.0 for day in days}

    series = moving_baseload(gas, effective, THRESHOLD)

    assert set(series) == set(days)
    newest = series[days[-1]]
    assert isinstance(newest, BaselinePoint)
    assert newest.kwh == pytest.approx(7.0)
    assert newest.kind is BaselineKind.MEASURED


def test_a_window_with_only_seven_warm_days_has_no_measured_value() -> None:
    days = _days(date(2026, 1, 1), 30)
    # Cold except the first 7 days.
    effective = {day: 20.0 if i < 7 else 0.0 for i, day in enumerate(days)}
    gas = {day: 7.0 for day in days}

    series = moving_baseload(gas, effective, THRESHOLD)

    # No window ever reaches 8 warm days, so there is nothing to measure or join.
    assert series == {}


def test_a_window_with_eight_warm_days_is_measured() -> None:
    days = _days(date(2026, 1, 1), 30)
    effective = {day: 20.0 if i < 8 else 0.0 for i, day in enumerate(days)}
    gas = {day: 7.0 for day in days}

    series = moving_baseload(gas, effective, THRESHOLD)

    assert series[days[7]].kind is BaselineKind.MEASURED
    assert series[days[7]].kwh == pytest.approx(7.0)


def test_a_winter_gap_is_interpolated_on_a_straight_line_between_measured_values() -> (
    None
):
    days = _days(date(2025, 1, 1), 500)
    # Warm for days 0-99 (gas 6) and 400-499 (gas 8), cold (gas 30) between.
    warm = [i < 100 or i >= 400 for i in range(500)]
    effective = {day: 20.0 if warm[i] else 2.0 for i, day in enumerate(days)}
    gas = {
        day: (30.0 if not warm[i] else 6.0 if i < 100 else 8.0)
        for i, day in enumerate(days)
    }

    series = moving_baseload(gas, effective, THRESHOLD)

    # Windows stay measured while 8 warm days remain in the trailing 120 days:
    # the last measured day of the first summer is 99 + 119 - 7 = 211. The next
    # measured day is 407 (8 warm days since day 400, all gas 8.0).
    assert series[days[211]].kind is BaselineKind.MEASURED
    assert series[days[212]].kind is BaselineKind.INTERPOLATED
    assert series[days[406]].kind is BaselineKind.INTERPOLATED
    assert series[days[407]].kind is BaselineKind.MEASURED
    assert series[days[407]].kwh == pytest.approx(8.0)
    midpoint = series[days[309]]  # halfway between days 211 and 407
    assert midpoint.kind is BaselineKind.INTERPOLATED
    assert midpoint.kwh == pytest.approx((series[days[211]].kwh + 8.0) / 2)


def test_days_after_the_last_measured_day_are_held_at_that_value() -> None:
    days = _days(date(2025, 1, 1), 300)
    effective = {day: 20.0 if i < 100 else 2.0 for i, day in enumerate(days)}
    gas = {day: 6.0 if i < 100 else 30.0 for i, day in enumerate(days)}

    series = moving_baseload(gas, effective, THRESHOLD)

    assert series[days[-1]].kind is BaselineKind.HELD
    assert series[days[-1]].kwh == pytest.approx(6.0)


def test_days_before_the_first_measured_day_are_held_at_the_first_value() -> None:
    days = _days(date(2025, 1, 1), 300)
    effective = {day: 2.0 if i < 100 else 20.0 for i, day in enumerate(days)}
    gas = {day: 30.0 if i < 100 else 6.0 for i, day in enumerate(days)}

    series = moving_baseload(gas, effective, THRESHOLD)

    assert series[days[0]].kind is BaselineKind.HELD
    assert series[days[0]].kwh == pytest.approx(6.0)
    assert series[days[107]].kind is BaselineKind.MEASURED


def test_measured_points_follow_a_drifting_seasonal_baseload_within_lag() -> None:
    days = _days(date(2024, 1, 1), 800)

    def planted(t: int) -> float:
        return 6 + 0.01 * t + 1.5 * math.sin(2 * math.pi * t / 365)

    # Warm (summer) stretches only: days 120..250 of each year.
    effective = {
        day: 20.0 if 120 <= i % 365 <= 250 else 3.0 for i, day in enumerate(days)
    }
    gas = {day: planted(i) for i, day in enumerate(days)}

    series = moving_baseload(gas, effective, THRESHOLD)

    measured = [
        (i, series[day].kwh)
        for i, day in enumerate(days)
        if series[day].kind is BaselineKind.MEASURED
    ]
    assert measured
    # A trailing window averages the past, so a measured value lags the planted
    # one: at most the drift over the window (0.01 * 130 = 1.3) plus the
    # seasonal swing across it (<= 1.5 * 2 = 3.0). The tolerance 2.5 covers it.
    for i, kwh in measured:
        assert abs(kwh - planted(i)) <= 2.5


def test_the_series_covers_every_calendar_day_including_gap_days() -> None:
    days = _days(date(2026, 5, 1), 150)
    present = [day for i, day in enumerate(days) if i % 10 != 5]  # gap days
    gas = {day: 7.0 for day in present}
    effective = {day: 20.0 for day in days}

    series = moving_baseload(gas, effective, THRESHOLD)

    assert set(series) == set(
        _days(min(present), (max(present) - min(present)).days + 1)
    )
    assert days[5] in series


NARROW_THRESHOLDS = (11.5, 12.0, 12.5, 13.0, 13.5)
NARROW_WEIGHTS = (0.4, 0.5, 0.6)
FIRST_DAY = date(2024, 10, 1)


def _summary(day: date, kwh: float, count: int | None = -1) -> ConsumptionSummary:
    if count == -1:
        count = local_day.expected_half_hour_count(day)
    return ConsumptionSummary(
        energy=Energy.gas,
        date=day,
        total_kwh=Decimal(str(round(kwh, 5))),
        half_hour_count=count,
    )


@dataclass(frozen=True)
class Planted:
    count: int = 730
    threshold: float = 12.5
    slope: float = 3.8
    weight: float = 0.5
    gas_noise: float = 0.4
    temperature_offset: float = 0.0


class Synthetic:
    """Days of planted heating behaviour: gas = a drifting seasonal baseload +
    slope * degree-days below the threshold of the blended effective temperature,
    plus a little seeded noise."""

    def __init__(self, planted: Planted | None = None) -> None:
        planted = planted or Planted()
        count, threshold, slope = planted.count, planted.threshold, planted.slope
        weight, gas_noise = planted.weight, planted.gas_noise
        temperature_offset = planted.temperature_offset
        rng = random.Random(7)
        self.days = [FIRST_DAY + timedelta(days=d) for d in range(count)]
        self.means = {
            day: 10
            + temperature_offset
            + 8 * math.sin(2 * math.pi * (t - 200) / 365)
            + rng.gauss(0, 2.0)
            for t, day in enumerate(self.days)
        }
        self.gas: dict[date, float] = {}
        for t, day in enumerate(self.days):
            previous = self.means.get(day - timedelta(days=1))
            effective = effective_temperature(self.means[day], previous, weight)
            baseload = 6 + 0.01 * t + 1.0 * math.sin(2 * math.pi * (t - 200) / 365)
            degree_days = (
                max(0.0, threshold - effective) if effective is not None else 0.0
            )
            self.gas[day] = baseload + slope * degree_days + rng.gauss(0, gas_noise)

    def summaries(
        self, omit: AbstractSet[date] = frozenset()
    ) -> list[ConsumptionSummary]:
        return [_summary(d, g) for d, g in self.gas.items() if d not in omit]

    def fit(
        self,
        summaries: list[ConsumptionSummary] | None = None,
        **grids: Sequence[float],
    ) -> HeatingFit:
        return fit_heating_model(
            self.summaries() if summaries is None else summaries,
            self.means,
            self.days[-1],
            **grids,
        )


def test_the_fit_recovers_a_planted_threshold_slope_and_weight() -> None:
    data = Synthetic()

    fit = data.fit()  # the full default grids

    assert fit.usable
    assert fit.threshold == pytest.approx(12.5, abs=0.3)
    assert fit.slope == pytest.approx(3.8, rel=0.05)
    assert fit.weight == pytest.approx(0.5, abs=0.1)
    assert fit.quality is not None
    assert fit.quality.r_squared > 0.9


def test_zero_gas_rows_and_partial_days_are_not_used() -> None:
    data = Synthetic(Planted(count=400))
    bad_zero = data.days[200:203]
    bad_partial = data.days[250:253]
    absent = data.fit(
        data.summaries(omit=set(bad_zero) | set(bad_partial)),
        thresholds=NARROW_THRESHOLDS,
        weights=NARROW_WEIGHTS,
    )
    with_bad_rows = [
        (
            _summary(s.date, 0.0)
            if s.date in bad_zero
            else (
                _summary(s.date, float(s.total_kwh), 20) if s.date in bad_partial else s
            )
        )
        for s in data.summaries()
    ]

    present = data.fit(
        with_bad_rows, thresholds=NARROW_THRESHOLDS, weights=NARROW_WEIGHTS
    )

    assert present == absent


def test_a_row_with_an_unknown_half_hour_count_is_used() -> None:
    data = Synthetic(Planted(count=400))
    unknown = [_summary(s.date, float(s.total_kwh), None) for s in data.summaries()]

    with_unknown = data.fit(
        unknown, thresholds=NARROW_THRESHOLDS, weights=NARROW_WEIGHTS
    )
    known = data.fit(thresholds=NARROW_THRESHOLDS, weights=NARROW_WEIGHTS)

    assert with_unknown == known
    assert known.quality is not None and known.quality.days_used > 300


def _with_away_stretch(
    data: Synthetic, stretch: list[date]
) -> list[ConsumptionSummary]:
    return [_summary(d, 0.5 if d in stretch else g) for d, g in data.gas.items()]


def test_an_away_stretch_is_reported_and_excluded_without_lowering_the_baseload() -> (
    None
):
    data = Synthetic()
    stretch = data.days[640:649]  # nine warm-weather days of almost no gas
    grids = {"thresholds": NARROW_THRESHOLDS, "weights": NARROW_WEIGHTS}

    without = data.fit(data.summaries(omit=set(stretch)), **grids)
    with_stretch = data.fit(_with_away_stretch(data, stretch), **grids)

    assert with_stretch.away_days == frozenset(stretch)
    assert with_stretch.quality == without.quality
    for day in data.days[650:700]:
        assert with_stretch.baseload[day].kwh == pytest.approx(
            without.baseload[day].kwh, abs=0.05
        )


def _assert_unusable(fit: HeatingFit, reason: UnusableReason) -> None:
    assert not fit.usable
    assert fit.reason is reason


def test_too_few_usable_days_is_unusable() -> None:
    data = Synthetic(Planted(count=730))
    recent = [s for s in data.summaries() if s.date >= data.days[-100]]

    fit = data.fit(recent, thresholds=NARROW_THRESHOLDS, weights=NARROW_WEIGHTS)

    _assert_unusable(fit, UnusableReason.TOO_FEW_DAYS)


def test_all_days_on_one_side_of_the_threshold_is_unusable() -> None:
    # A cold year with a few warm days: under 30 at or above the threshold.
    data = Synthetic(Planted(count=365, temperature_offset=-12.0))
    warm_days = data.days[100:125]
    for day in warm_days:
        data.means[day] = 20.0
        data.gas[day] = 7.0

    fit = data.fit(thresholds=NARROW_THRESHOLDS, weights=NARROW_WEIGHTS)

    _assert_unusable(fit, UnusableReason.TOO_FEW_DAYS_ON_ONE_SIDE)


def test_noisy_gas_that_the_model_explains_poorly_is_unusable() -> None:
    data = Synthetic(Planted(gas_noise=25.0))

    fit = data.fit(thresholds=NARROW_THRESHOLDS, weights=NARROW_WEIGHTS)

    assert fit.quality is not None
    _assert_unusable(fit, UnusableReason.POOR_FIT)


def test_a_threshold_at_the_edge_of_the_search_grid_is_unusable() -> None:
    data = Synthetic(Planted(threshold=12.5))

    fit = data.fit(thresholds=(13.5, 14.0, 14.5, 15.0, 15.5), weights=NARROW_WEIGHTS)

    _assert_unusable(fit, UnusableReason.THRESHOLD_AT_EDGE)


def test_gas_that_falls_as_it_gets_colder_has_a_non_positive_slope_and_is_unusable() -> (
    None
):
    data = Synthetic(Planted(slope=-1.0))

    fit = data.fit(thresholds=NARROW_THRESHOLDS, weights=NARROW_WEIGHTS)

    _assert_unusable(fit, UnusableReason.NON_POSITIVE_SLOPE)


def test_the_blend_weight_is_bounded_at_the_top_of_the_default_grid() -> None:
    data = Synthetic(Planted(weight=0.95))

    fit = data.fit(thresholds=NARROW_THRESHOLDS)

    assert fit.weight is not None
    assert fit.weight <= 0.8
    assert WEIGHT_MAX == 0.8
    assert max(WEIGHTS) == 0.8


def _flat_summaries(count: int, kwh: float = 7.0) -> list[ConsumptionSummary]:
    return [_summary(FIRST_DAY + timedelta(days=d), kwh) for d in range(count)]


def _constant_means(count: int, mean: float) -> dict[date, float]:
    return {FIRST_DAY + timedelta(days=d): mean for d in range(count)}


def _fit_flat(count: int, mean: float, kwh: float = 7.0) -> HeatingFit:
    return fit_heating_model(
        _flat_summaries(count, kwh),
        _constant_means(count, mean),
        FIRST_DAY + timedelta(days=count - 1),
    )


def _assert_no_fit_was_made(fit: HeatingFit, reason: UnusableReason) -> None:
    _assert_unusable(fit, reason)
    assert fit.threshold is None
    assert fit.slope is None
    assert fit.weight is None
    assert fit.quality is None
    assert fit.baseload == {}


def test_no_summaries_is_unusable_with_no_numbers() -> None:
    fit = fit_heating_model([], {}, FIRST_DAY)

    _assert_no_fit_was_made(fit, UnusableReason.TOO_FEW_DAYS)


def test_only_unusable_rows_is_unusable_for_too_few_days() -> None:
    rows = [_summary(FIRST_DAY + timedelta(days=d), 0.0) for d in range(200)]

    fit = fit_heating_model(rows, _constant_means(200, 5.0), FIRST_DAY)

    _assert_no_fit_was_made(fit, UnusableReason.TOO_FEW_DAYS)


def test_gas_days_with_no_temperatures_at_all_are_too_few_days() -> None:
    rows = _flat_summaries(200)

    fit = fit_heating_model(rows, {}, FIRST_DAY + timedelta(days=199))

    _assert_no_fit_was_made(fit, UnusableReason.TOO_FEW_DAYS)


def test_a_year_warmer_than_every_threshold_has_no_fit() -> None:
    fit = _fit_flat(365, mean=30.0)

    _assert_no_fit_was_made(fit, UnusableReason.NO_FIT)


def test_under_150_days_warmer_than_every_threshold_is_too_few_days() -> None:
    fit = _fit_flat(100, mean=30.0)

    _assert_no_fit_was_made(fit, UnusableReason.TOO_FEW_DAYS)


def test_a_cold_only_history_with_no_measurable_baseload_has_no_fit() -> None:
    fit = _fit_flat(365, mean=0.0, kwh=20.0)

    _assert_no_fit_was_made(fit, UnusableReason.NO_FIT)


def test_dropping_away_days_that_leave_nothing_below_a_threshold_has_no_fit() -> None:
    # 100 warm days at 7 kWh, then 100 cold days of almost no gas: every cold
    # day is an away day, so the second fit has no day below any threshold.
    count = 200
    rows = [
        _summary(FIRST_DAY + timedelta(days=d), 7.0 if d < 100 else 0.5)
        for d in range(count)
    ]
    means = {
        FIRST_DAY + timedelta(days=d): 20.0 if d < 100 else 0.0 for d in range(count)
    }

    fit = fit_heating_model(
        rows,
        means,
        FIRST_DAY + timedelta(days=count - 1),
        thresholds=NARROW_THRESHOLDS,
        weights=NARROW_WEIGHTS,
    )

    _assert_unusable(fit, UnusableReason.NO_FIT)
    assert len(fit.away_days) == 100
    assert fit.threshold is None


def test_a_guard_failure_after_a_real_fit_keeps_the_fitted_numbers() -> None:
    data = Synthetic(Planted(gas_noise=25.0))

    fit = data.fit(thresholds=NARROW_THRESHOLDS, weights=NARROW_WEIGHTS)

    _assert_unusable(fit, UnusableReason.POOR_FIT)
    assert fit.threshold is not None
    assert fit.slope is not None
    assert fit.weight is not None
    assert fit.baseload


def test_the_fit_follows_only_the_most_recent_365_days() -> None:
    old = Synthetic(Planted(threshold=10.0, slope=6.0))
    recent = Synthetic(Planted(threshold=14.0, slope=2.5))
    # The regime changed at day 300; the 365-day window starts at day 365.
    recent.gas = {
        day: (old.gas if i < 300 else recent.gas)[day]
        for i, day in enumerate(recent.days)
    }

    fit = recent.fit(thresholds=(13.0, 13.5, 14.0, 14.5, 15.0), weights=(0.5,))

    assert fit.threshold == pytest.approx(14.0, abs=0.5)
    assert fit.slope == pytest.approx(2.5, rel=0.1)


def test_rows_after_as_of_are_ignored() -> None:
    data = Synthetic()
    as_of = data.days[600]
    grids = {"thresholds": NARROW_THRESHOLDS, "weights": NARROW_WEIGHTS}
    wild = [_summary(s.date, 500.0) if s.date > as_of else s for s in data.summaries()]
    until_as_of = [s for s in data.summaries() if s.date <= as_of]

    with_future = fit_heating_model(wild, data.means, as_of, **grids)
    without_future = fit_heating_model(until_as_of, data.means, as_of, **grids)

    assert with_future == without_future


def test_a_threshold_at_the_top_edge_of_the_search_grid_is_unusable() -> None:
    data = Synthetic(Planted(threshold=12.5))

    fit = data.fit(thresholds=(9.5, 10.0, 10.5, 11.0, 11.5), weights=NARROW_WEIGHTS)

    _assert_unusable(fit, UnusableReason.THRESHOLD_AT_EDGE)


_COLD_TEMPERATURES = (2.0, 4.0, 6.0, 8.0, 9.0, 5.0, 3.0, 7.0)
# The noise sign of each cold temperature: the degrees below 12.5 of the
# positive ones (10.5, 6.5, 3.5, 7.5) and of the negative ones (8.5, 4.5, 9.5,
# 5.5) both sum to 28, so over whole cycles the noise is uncorrelated with them.
_NOISE_SIGNS = {2.0: 1, 4.0: -1, 6.0: 1, 8.0: -1, 9.0: 1, 5.0: 1, 3.0: -1, 7.0: -1}


def _step_fit(
    warm: int, cold: int, noise: float = 0.0, slope: float = 3.8
) -> HeatingFit:
    """Exact data (no noise): `warm` days at 20 C then `cold` days at 2-9 C, gas
    7 + 3.8 * degrees below 12.5. With a zero blend weight the fit recovers the
    threshold exactly, so the day counts either side of it are known: the first
    day has no previous day and is unused, leaving warm - 1 days at or above the
    threshold, cold days below, and warm + cold - 1 days used. `noise` adds a
    fixed +/- noise to each cold day's gas, balanced so that it is uncorrelated
    with the temperature and sums to zero over every 8 cold days."""
    days = [FIRST_DAY + timedelta(days=d) for d in range(warm + cold)]
    means = {
        day: 20.0 if i < warm else _COLD_TEMPERATURES[i % len(_COLD_TEMPERATURES)]
        for i, day in enumerate(days)
    }
    rows = [
        _summary(
            day,
            7.0
            + slope * max(0.0, 12.5 - means[day])
            + (noise * _NOISE_SIGNS[means[day]] if i >= warm else 0.0),
        )
        for i, day in enumerate(days)
    ]
    return fit_heating_model(
        rows, means, days[-1], thresholds=NARROW_THRESHOLDS, weights=(0.0,)
    )


def test_exactly_150_usable_days_is_enough() -> None:
    fit = _step_fit(warm=60, cold=91)

    assert fit.quality is not None
    assert fit.quality.days_used == 150
    assert fit.usable


def test_149_usable_days_is_too_few() -> None:
    fit = _step_fit(warm=60, cold=90)

    assert fit.quality is not None
    assert fit.quality.days_used == 149
    _assert_unusable(fit, UnusableReason.TOO_FEW_DAYS)


def test_exactly_30_days_at_or_above_the_threshold_is_enough() -> None:
    fit = _step_fit(warm=31, cold=170)

    assert fit.quality is not None
    assert fit.quality.days_above_threshold == 30
    assert fit.usable


def test_29_days_at_or_above_the_threshold_is_too_few_on_that_side() -> None:
    fit = _step_fit(warm=30, cold=170)

    assert fit.quality is not None
    assert fit.quality.days_above_threshold == 29
    _assert_unusable(fit, UnusableReason.TOO_FEW_DAYS_ON_ONE_SIDE)


def test_exactly_30_days_below_the_threshold_is_enough() -> None:
    fit = _step_fit(warm=121, cold=30)

    assert fit.quality is not None
    assert fit.quality.days_below_threshold == 30
    assert fit.usable


def test_29_days_below_the_threshold_is_too_few_on_that_side() -> None:
    fit = _step_fit(warm=122, cold=29)

    assert fit.quality is not None
    assert fit.quality.days_below_threshold == 29
    _assert_unusable(fit, UnusableReason.TOO_FEW_DAYS_ON_ONE_SIDE)


@pytest.mark.parametrize("weight", [0.9, -0.05])
def test_a_weight_outside_the_allowed_range_is_rejected(weight: float) -> None:
    data = Synthetic(Planted(count=400))

    with pytest.raises(ValueError, match="between 0 and 0.8"):
        data.fit(thresholds=NARROW_THRESHOLDS, weights=[weight])


def test_a_day_exactly_one_margin_above_the_threshold_is_warm() -> None:
    days = _days(date(2026, 1, 1), 8)
    gas = {day: 7.0 for day in days}

    on_the_margin = moving_baseload(gas, {day: 13.0 for day in days}, 12.0)
    just_under = moving_baseload(gas, {day: 12.9 for day in days}, 12.0)

    assert on_the_margin[days[-1]].kind is BaselineKind.MEASURED
    assert just_under == {}


def test_away_days_are_those_under_a_third_of_the_baseload() -> None:
    # Exact data: 60 warm days of 6 kWh (so the baseload is exactly 6), then 100
    # cold days. Three cold days are made low: 0.30, exactly 1/3 and 0.36 of the
    # baseload. Cold days are never warm days, so the baseload stays 6.
    warm, cold = 60, 100
    days = [FIRST_DAY + timedelta(days=d) for d in range(warm + cold)]
    means = {
        day: 20.0 if i < warm else _COLD_TEMPERATURES[i % len(_COLD_TEMPERATURES)]
        for i, day in enumerate(days)
    }
    gas = {day: 6.0 + 3.8 * max(0.0, 12.5 - means[day]) for day in days}
    below, exactly, above = days[70], days[80], days[90]
    gas[below], gas[exactly], gas[above] = 6.0 * 0.30, 6.0 / 3, 6.0 * 0.36

    fit = fit_heating_model(
        [_summary(day, kwh) for day, kwh in gas.items()],
        means,
        days[-1],
        thresholds=NARROW_THRESHOLDS,
        weights=(0.0,),
    )

    assert fit.away_days == frozenset({below})


def test_an_r_squared_just_above_one_half_stays_usable() -> None:
    fit = _step_fit(warm=60, cold=104, noise=8.5, slope=2.0)

    assert fit.quality is not None
    assert 0.5 < fit.quality.r_squared < 0.6
    assert fit.usable


def test_an_r_squared_just_below_one_half_is_a_poor_fit() -> None:
    fit = _step_fit(warm=60, cold=104, noise=10.5, slope=2.0)

    assert fit.quality is not None
    assert 0.4 < fit.quality.r_squared < 0.5
    _assert_unusable(fit, UnusableReason.POOR_FIT)


def test_electricity_summaries_are_ignored() -> None:
    data = Synthetic(Planted(count=400))
    grids = {"thresholds": NARROW_THRESHOLDS, "weights": NARROW_WEIGHTS}
    gas_rows = data.summaries()
    electricity_rows = [
        ConsumptionSummary(
            energy=Energy.electricity,
            date=s.date,
            total_kwh=s.total_kwh * 1000,
            half_hour_count=s.half_hour_count,
        )
        for s in gas_rows
    ]

    mixed = data.fit(electricity_rows + gas_rows, **grids)
    mixed_other_way = data.fit(gas_rows + electricity_rows, **grids)

    assert mixed == data.fit(gas_rows, **grids)
    assert mixed_other_way == mixed


def test_a_day_with_a_nan_mean_temperature_is_the_same_as_the_day_being_absent() -> (
    None
):
    data = Synthetic(Planted(count=400))
    grids = {"thresholds": NARROW_THRESHOLDS, "weights": NARROW_WEIGHTS}
    absent = dict(data.means)
    del absent[data.days[200]]
    nan_means = {**data.means, data.days[200]: math.nan}
    rows = data.summaries()

    with_nan = fit_heating_model(rows, nan_means, data.days[-1], **grids)
    without = fit_heating_model(rows, absent, data.days[-1], **grids)

    assert with_nan == without


def test_the_default_search_grids_are_8_to_18_by_tenths_and_0_to_point_8_by_twentieths() -> (
    None
):
    assert THRESHOLDS == tuple(round(8.0 + 0.1 * i, 1) for i in range(101))
    assert (THRESHOLDS[0], THRESHOLDS[-1], len(THRESHOLDS)) == (8.0, 18.0, 101)
    assert WEIGHTS == tuple(round(0.05 * i, 2) for i in range(17))
    assert (WEIGHTS[0], WEIGHTS[-1], len(WEIGHTS)) == (0.0, 0.8, 17)

import copy
from datetime import date

import pytest

from octopus_app.data.heating_model import (
    baseload_and_away_days,
    effective_temperatures,
)

from .test_heating_model_fit import (
    NARROW_THRESHOLDS,
    NARROW_WEIGHTS,
    Synthetic,
    _with_away_stretch,
)

AWAY_GAS = 0.5  # a holiday day: almost no gas
THRESHOLD = 12.5


def _holiday() -> tuple[Synthetic, list[date]]:
    data = Synthetic()
    return data, data.days[640:649]  # nine warm-weather days of almost no gas


def _gas_with_holiday(data: Synthetic, stretch: list[date]) -> dict[date, float]:
    return {d: AWAY_GAS if d in stretch else g for d, g in data.gas.items()}


def test_it_matches_the_fits_away_days_and_baseload_at_the_fits_threshold_and_weight() -> (
    None
):
    data, stretch = _holiday()
    fit = data.fit(
        _with_away_stretch(data, stretch),
        thresholds=NARROW_THRESHOLDS,
        weights=NARROW_WEIGHTS,
    )
    assert fit.threshold is not None
    assert fit.weight is not None
    effective = effective_temperatures(data.means, fit.weight)

    baseload, away = baseload_and_away_days(
        _gas_with_holiday(data, stretch), effective, fit.threshold
    )

    assert away == fit.away_days == frozenset(stretch)
    assert set(baseload) == set(fit.baseload)
    for day, point in fit.baseload.items():
        assert baseload[day].kwh == pytest.approx(point.kwh, abs=0.05)
        assert baseload[day].kind is point.kind


def test_away_days_are_excluded_and_do_not_pull_the_baseload_down() -> None:
    data, stretch = _holiday()
    effective = effective_temperatures(data.means, 0.5)
    without = {d: g for d, g in data.gas.items() if d not in stretch}

    baseload, away = baseload_and_away_days(
        _gas_with_holiday(data, stretch), effective, THRESHOLD
    )
    expected, no_away = baseload_and_away_days(without, effective, THRESHOLD)

    assert away == frozenset(stretch)
    assert no_away == frozenset()
    for day in data.days[650:700]:
        assert baseload[day].kwh == pytest.approx(expected[day].kwh, abs=0.05)


def test_no_gas_gives_no_baseload_and_no_away_days() -> None:
    assert baseload_and_away_days({}, {}, THRESHOLD) == ({}, frozenset())


def test_a_history_with_too_few_warm_days_to_measure_a_baseload_gives_nothing() -> None:
    data = Synthetic()
    cold = {d: 0.0 for d in data.days}  # never warm enough for a baseload

    assert baseload_and_away_days(data.gas, cold, THRESHOLD) == ({}, frozenset())


def test_the_same_inputs_give_the_same_answer_and_are_not_changed() -> None:
    data, stretch = _holiday()
    gas = _gas_with_holiday(data, stretch)
    effective = effective_temperatures(data.means, 0.5)
    gas_before, effective_before = copy.deepcopy(gas), copy.deepcopy(effective)

    first = baseload_and_away_days(gas, effective, THRESHOLD)
    second = baseload_and_away_days(gas, effective, THRESHOLD)

    assert first == second
    assert gas == gas_before
    assert effective == effective_before

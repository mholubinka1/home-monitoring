import math
from datetime import date, timedelta

import pytest

from octopus_app.data.heating_model import BaselineKind, BaselinePoint
from octopus_app.data.heating_weeks import WeekRow, WeekStatus, weekly_rows

THRESHOLD = 12.0
BASELOAD = 7.0
SLOPE = 3.0
MONDAY = date(2026, 1, 5)  # a Monday


def _days(start: date, count: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(count)]


def _baseload(days: list[date]) -> dict[date, BaselinePoint]:
    return {d: BaselinePoint(BASELOAD, BaselineKind.MEASURED) for d in days}


def test_a_week_with_five_complete_days_is_shown_with_per_day_averages() -> None:
    # Monday to Friday complete; the weekend has no gas reading.
    days = _days(MONDAY, 5)
    # 5 C against a 12 C threshold is 7 degree-days: 7 + 3 * 7 = 28 kWh.
    gas = {d: BASELOAD + SLOPE * 7 for d in days}
    effective = {d: 5.0 for d in days}

    rows = weekly_rows(gas, effective, _baseload(days), frozenset(), THRESHOLD)

    assert len(rows) == 1
    week = rows[0]
    assert week.week_start == MONDAY
    assert week.status is WeekStatus.SHOWN
    assert week.complete_days == 5
    assert week.avg_gas == pytest.approx(28.0)
    assert week.avg_degree_days == pytest.approx(7.0)
    assert week.avg_baseload == pytest.approx(7.0)
    assert week.avg_heating_gas == pytest.approx(21.0)
    assert week.efficiency == pytest.approx(3.0)


def _week(
    start: date,
    count: int,
    *,
    temperature: float = 5.0,
    heating_gas: float = 21.0,
    baseload: float = BASELOAD,
) -> tuple[dict[date, float], dict[date, float], dict[date, BaselinePoint]]:
    days = _days(start, count)
    return (
        {d: baseload + heating_gas for d in days},
        {d: temperature for d in days},
        {d: BaselinePoint(baseload, BaselineKind.MEASURED) for d in days},
    )


def _only_week(
    gas: dict[date, float],
    effective: dict[date, float],
    baseload: dict[date, BaselinePoint],
    away: frozenset[date] = frozenset(),
) -> WeekRow:
    rows = weekly_rows(gas, effective, baseload, away, THRESHOLD)
    assert len(rows) == 1
    return rows[0]


def test_a_week_with_four_complete_days_has_too_few_days_but_still_reports_averages() -> (
    None
):
    week = _only_week(*_week(MONDAY, 4))

    assert week.status is WeekStatus.TOO_FEW_DAYS
    assert week.complete_days == 4
    assert week.avg_gas == pytest.approx(28.0)
    assert week.efficiency == pytest.approx(3.0)


def test_a_week_with_exactly_five_complete_days_is_counted_enough() -> None:
    assert _only_week(*_week(MONDAY, 5)).status is WeekStatus.SHOWN


def test_a_week_with_exactly_one_and_a_half_degree_days_a_day_is_shown() -> None:
    week = _only_week(*_week(MONDAY, 5, temperature=10.5))

    assert week.avg_degree_days == pytest.approx(1.5)
    assert week.status is WeekStatus.SHOWN


def test_a_week_with_just_under_one_and_a_half_degree_days_a_day_is_too_mild() -> None:
    week = _only_week(*_week(MONDAY, 5, temperature=10.51))

    assert week.status is WeekStatus.TOO_MILD


def test_a_week_with_exactly_half_a_kwh_of_heating_gas_a_day_is_shown() -> None:
    week = _only_week(*_week(MONDAY, 5, heating_gas=0.5))

    assert week.status is WeekStatus.SHOWN


def test_a_week_with_under_half_a_kwh_of_heating_gas_a_day_is_heating_off() -> None:
    week = _only_week(*_week(MONDAY, 5, heating_gas=0.49))

    assert week.status is WeekStatus.HEATING_OFF


def test_a_week_with_too_few_days_and_a_mild_climate_reports_too_few_days_first() -> (
    None
):
    week = _only_week(*_week(MONDAY, 4, temperature=20.0, heating_gas=0.0))

    assert week.status is WeekStatus.TOO_FEW_DAYS


def test_each_week_uses_its_own_baseload_and_the_one_threshold() -> None:
    second = MONDAY + timedelta(days=7)
    gas_1, eff_1, base_1 = _week(MONDAY, 5, baseload=6.0, heating_gas=20.0)
    gas_2, eff_2, base_2 = _week(second, 5, baseload=8.0, heating_gas=20.0)

    rows = weekly_rows(
        gas_1 | gas_2, eff_1 | eff_2, base_1 | base_2, frozenset(), THRESHOLD
    )

    assert [r.avg_baseload for r in rows] == [pytest.approx(6.0), pytest.approx(8.0)]
    assert [r.avg_gas for r in rows] == [pytest.approx(26.0), pytest.approx(28.0)]
    assert [r.avg_heating_gas for r in rows] == [pytest.approx(20.0)] * 2
    assert [r.avg_degree_days for r in rows] == [pytest.approx(7.0)] * 2


def test_a_week_with_heating_gas_measured_against_a_day_s_own_baseload() -> None:
    gas, effective, baseload = _week(MONDAY, 5)
    baseload[MONDAY] = BaselinePoint(10.0, BaselineKind.INTERPOLATED)

    week = _only_week(gas, effective, baseload)

    # Four days of 21 and one of 18.
    assert week.avg_heating_gas == pytest.approx(20.4)
    assert week.avg_baseload == pytest.approx(7.6)


def test_away_days_are_excluded_from_the_averages_and_count_against_the_five() -> None:
    gas, effective, baseload = _week(MONDAY, 7)
    away = frozenset(_days(MONDAY, 2))
    for day in away:
        gas[day] = 0.2  # a holiday: far below its baseload

    week = _only_week(gas, effective, baseload, away)

    assert week.complete_days == 5
    assert week.status is WeekStatus.SHOWN
    assert week.avg_gas == pytest.approx(28.0)
    assert week.avg_heating_gas == pytest.approx(21.0)


def test_a_week_with_six_gas_days_and_two_away_has_too_few_days() -> None:
    gas, effective, baseload = _week(MONDAY, 6)

    week = _only_week(gas, effective, baseload, frozenset(_days(MONDAY, 2)))

    assert week.complete_days == 4
    assert week.status is WeekStatus.TOO_FEW_DAYS


def test_a_day_without_an_effective_temperature_is_not_counted() -> None:
    gas, effective, baseload = _week(MONDAY, 6)
    del effective[MONDAY]

    week = _only_week(gas, effective, baseload)

    assert week.complete_days == 5


def test_a_day_without_a_baseload_is_not_counted() -> None:
    gas, effective, baseload = _week(MONDAY, 6)
    del baseload[MONDAY]

    week = _only_week(gas, effective, baseload)

    assert week.complete_days == 5


def test_rows_cover_the_whole_history_oldest_first_skipping_weeks_without_gas() -> None:
    later = MONDAY + timedelta(days=21)  # two weeks on: the week between has no gas
    gas_1, eff_1, base_1 = _week(MONDAY, 5)
    gas_2, eff_2, base_2 = _week(later, 5)

    rows = weekly_rows(
        gas_2 | gas_1, eff_2 | eff_1, base_2 | base_1, frozenset(), THRESHOLD
    )

    assert [r.week_start for r in rows] == [MONDAY, later]


def test_weeks_run_from_monday_to_sunday() -> None:
    sunday = MONDAY + timedelta(days=6)
    gas, effective, baseload = _week(sunday, 2)  # Sunday, then the next Monday

    rows = weekly_rows(gas, effective, baseload, frozenset(), THRESHOLD)

    assert [r.week_start for r in rows] == [MONDAY, MONDAY + timedelta(days=7)]
    assert [r.complete_days for r in rows] == [1, 1]


def test_a_week_of_only_away_days_has_too_few_days_and_no_averages() -> None:
    gas, effective, baseload = _week(MONDAY, 7)

    week = _only_week(gas, effective, baseload, frozenset(gas))

    assert week.status is WeekStatus.TOO_FEW_DAYS
    assert week.complete_days == 0
    assert week.avg_gas is None
    assert week.avg_degree_days is None
    assert week.avg_baseload is None
    assert week.avg_heating_gas is None
    assert week.efficiency is None


def test_a_week_with_no_degree_days_has_no_efficiency() -> None:
    week = _only_week(*_week(MONDAY, 5, temperature=20.0))

    assert week.avg_degree_days == 0.0
    assert week.efficiency is None
    assert week.status is WeekStatus.TOO_MILD


def test_no_gas_gives_no_rows() -> None:
    assert weekly_rows({}, {}, {}, frozenset(), THRESHOLD) == []


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_gas_raises(bad: float) -> None:
    gas, effective, baseload = _week(MONDAY, 5)
    gas[MONDAY] = bad

    with pytest.raises(ValueError, match="finite"):
        weekly_rows(gas, effective, baseload, frozenset(), THRESHOLD)


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_a_non_finite_effective_temperature_raises(bad: float) -> None:
    gas, effective, baseload = _week(MONDAY, 5)
    effective[MONDAY] = bad

    with pytest.raises(ValueError, match="finite"):
        weekly_rows(gas, effective, baseload, frozenset(), THRESHOLD)


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_a_non_finite_threshold_raises(bad: float) -> None:
    gas, effective, baseload = _week(MONDAY, 5)

    with pytest.raises(ValueError, match="finite"):
        weekly_rows(gas, effective, baseload, frozenset(), bad)

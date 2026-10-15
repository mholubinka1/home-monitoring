import math
from datetime import date, timedelta

import pytest

from octopus_app.data.heating_forecast import (
    ForecastDay,
    ForecastModel,
    ForecastWindow,
    project_expected_gas,
)
from octopus_app.data.heating_model import WEIGHT_MAX

THRESHOLD = 12.0
SLOPE = 3.0
BASELOAD = 7.0
TODAY = date(2026, 10, 15)


def _forecast(*means: float, start: date = TODAY) -> dict[date, float]:
    return {start + timedelta(days=i): mean for i, mean in enumerate(means)}


def _project(
    forecast: dict[date, float],
    observed: dict[date, float] | None = None,
    *,
    days: int = 7,
    weight: float = 0.0,
) -> list[ForecastDay]:
    return project_expected_gas(
        forecast,
        observed or {},
        ForecastWindow(TODAY, days),
        ForecastModel(THRESHOLD, SLOPE, weight, BASELOAD),
    )


def test_seven_forecast_days_give_seven_rows_with_baseload_plus_heating() -> None:
    forecast = _forecast(20.0, 15.0, 12.0, 10.0, 5.0, 0.0, -3.0)

    rows = _project(forecast, {TODAY - timedelta(days=1): 10.0})

    assert [r.date for r in rows] == sorted(forecast)
    assert [r.forecast_mean for r in rows] == list(forecast.values())
    # Warm and at-threshold days need no heating; colder days add 3 kWh a degree.
    assert [r.expected_gas for r in rows] == [
        pytest.approx(v) for v in (7.0, 7.0, 7.0, 13.0, 28.0, 43.0, 52.0)
    ]


def test_the_first_days_yesterday_comes_from_the_observed_means() -> None:
    forecast = _forecast(10.0, 10.0)
    observed = {TODAY - timedelta(days=1): 0.0}

    rows = _project(forecast, observed, weight=0.5)

    # Day one blends today's 10 with the observed 0; day two with the forecast 10.
    assert [r.effective_temperature for r in rows] == [
        pytest.approx(5.0),
        pytest.approx(10.0),
    ]


def test_an_observed_mean_beats_the_forecast_for_a_day_that_is_in_both() -> None:
    forecast = _forecast(10.0, 10.0)
    observed = {TODAY: 0.0, TODAY - timedelta(days=1): 10.0}

    rows = _project(forecast, observed, weight=0.5)

    assert rows[1].effective_temperature == pytest.approx(5.0)


def test_a_missing_forecast_day_is_skipped_and_so_is_the_next_without_yesterday() -> (
    None
):
    forecast = _forecast(10.0, 10.0, 10.0, 10.0)
    del forecast[TODAY + timedelta(days=1)]

    rows = _project(forecast, {TODAY - timedelta(days=1): 10.0}, days=4)

    # Day two has no mean; day three has a mean but no yesterday anywhere.
    assert [r.date for r in rows] == [
        TODAY,
        TODAY + timedelta(days=3),
    ]


def test_the_day_after_a_missing_forecast_day_is_kept_when_observed_has_yesterday() -> (
    None
):
    forecast = _forecast(10.0, 10.0, 10.0)
    gap = TODAY + timedelta(days=1)
    del forecast[gap]

    rows = _project(forecast, {TODAY - timedelta(days=1): 10.0, gap: 8.0}, days=3)

    assert [r.date for r in rows] == [TODAY, TODAY + timedelta(days=2)]


def test_a_first_day_with_no_yesterday_anywhere_is_skipped() -> None:
    rows = _project(_forecast(10.0, 10.0), days=2)

    assert [r.date for r in rows] == [TODAY + timedelta(days=1)]


def test_a_heavier_weight_blends_in_more_of_yesterday() -> None:
    forecast = _forecast(10.0)
    observed = {TODAY - timedelta(days=1): 0.0}

    plain = _project(forecast, observed, weight=0.0)[0]
    blended = _project(forecast, observed, weight=0.8)[0]

    assert plain.effective_temperature == pytest.approx(10.0)
    assert blended.effective_temperature == pytest.approx(2.0)
    assert blended.expected_gas == pytest.approx(BASELOAD + SLOPE * 10.0)


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_a_non_finite_forecast_mean_is_treated_as_missing(bad: float) -> None:
    forecast = _forecast(bad, 10.0)

    rows = _project(forecast, {TODAY - timedelta(days=1): 10.0}, days=2)

    # Day two's yesterday is the non-finite forecast, so it is skipped as well.
    assert not rows


@pytest.mark.parametrize("bad", [math.nan, -math.inf])
def test_a_non_finite_observed_mean_is_treated_as_missing(bad: float) -> None:
    rows = _project(_forecast(10.0), {TODAY - timedelta(days=1): bad}, days=1)

    assert not rows


@pytest.mark.parametrize("days", [0, -3])
def test_no_days_gives_no_rows(days: int) -> None:
    assert not _project(_forecast(10.0), {TODAY - timedelta(days=1): 10.0}, days=days)


@pytest.mark.parametrize("weight", [-0.01, WEIGHT_MAX + 0.01, math.nan])
def test_a_weight_outside_zero_to_the_maximum_raises(weight: float) -> None:
    with pytest.raises(ValueError, match="weight"):
        ForecastModel(THRESHOLD, SLOPE, weight, BASELOAD)


@pytest.mark.parametrize("weight", [0.0, WEIGHT_MAX])
def test_the_weight_limits_themselves_are_allowed(weight: float) -> None:
    rows = _project(_forecast(10.0), {TODAY - timedelta(days=1): 10.0}, weight=weight)

    assert len(rows) == 1

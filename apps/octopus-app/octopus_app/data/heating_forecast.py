"""Pure projection of expected gas from a weather forecast: no database access."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from octopus_app.data.heating_model import WEIGHT_MAX, effective_temperature


@dataclass(frozen=True)
class ForecastDay:
    date: date
    forecast_mean: float
    effective_temperature: float
    expected_gas: float


@dataclass(frozen=True)
class ForecastWindow:
    """The first date to project and how many days from it."""

    start: date
    days: int


@dataclass(frozen=True)
class ForecastModel:
    """The fitted heating model, with the baseload to project from.

    Raises ValueError when the weight is outside 0..WEIGHT_MAX."""

    threshold: float
    slope: float
    weight: float
    baseload: float

    def __post_init__(self) -> None:
        if not 0 <= self.weight <= WEIGHT_MAX:
            raise ValueError(
                f"weight must be between 0 and {WEIGHT_MAX}, not {self.weight}"
            )


def _yesterdays_mean(
    day: date,
    forecast_means: Mapping[date, float],
    observed_means: Mapping[date, float],
) -> float | None:
    """The previous day's observed mean when there is one, else its forecast."""
    previous = day - timedelta(days=1)
    return observed_means.get(previous, forecast_means.get(previous))


def project_expected_gas(
    forecast_means: Mapping[date, float],
    observed_means: Mapping[date, float],
    window: ForecastWindow,
    model: ForecastModel,
) -> list[ForecastDay]:
    """The expected gas of each day of the window: baseload plus slope times the
    degree-days below the threshold of the day's effective temperature.

    The day's mean comes from the forecast; yesterday's from the observed means
    when present, else from the forecast. A day with no mean for itself or for
    yesterday, or a non-finite one, is skipped and has no row. A window of no
    days gives []."""
    projection = []
    for offset in range(window.days):
        day = window.start + timedelta(days=offset)
        mean = forecast_means.get(day)
        effective = effective_temperature(
            mean,
            _yesterdays_mean(day, forecast_means, observed_means),
            model.weight,
        )
        if mean is None or effective is None:
            continue
        projection.append(
            ForecastDay(
                date=day,
                forecast_mean=mean,
                effective_temperature=effective,
                expected_gas=model.baseload
                + model.slope * max(0.0, model.threshold - effective),
            )
        )
    return projection

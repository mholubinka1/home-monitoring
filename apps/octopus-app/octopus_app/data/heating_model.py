"""Pure heating-model calculations: no database access."""

from collections.abc import Mapping
from datetime import date, timedelta


def effective_temperature(
    todays_mean: float | None, yesterdays_mean: float | None, weight: float
) -> float | None:
    """Today's mean blended with yesterday's: the house's slow thermal response
    means yesterday's weather still affects today's heating."""
    if todays_mean is None or yesterdays_mean is None:
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

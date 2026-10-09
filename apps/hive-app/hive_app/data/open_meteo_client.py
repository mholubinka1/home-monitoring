from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import requests
from pydantic import BaseModel

from hive_app.common.config import LocationSettings
from hive_app.data.model import WeatherForecastDay, WeatherObservation
from hive_app.data.weather_types import (
    REQUEST_TIMEOUT_SECONDS,
    FiniteFloat,
    location_key,
)

HOURLY_VARIABLES = (
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "wind_speed_10m",
    "precipitation",
    "shortwave_radiation",
    "cloud_cover",
    "sunshine_duration",
)
HOURLY_FIELDS = ",".join(HOURLY_VARIABLES)
# Each live run re-reads this many past hours (plus the current hour's stamp),
# so an hour missed by an earlier run -- the daily restart, an outage -- is
# filled by the next one.
LOOKBACK_HOURS = 24
DAILY_FIELDS = "temperature_2m_max,temperature_2m_mean"


def _values_or_nulls(
    values: list[FiniteFloat | None] | None, length: int
) -> list[float | None]:
    """An array Open-Meteo omitted means no value for any entry."""
    return [None] * length if values is None else list(values)


class OpenMeteoHourly(BaseModel):
    time: list[datetime]
    temperature_2m: list[FiniteFloat | None] | None = None
    relative_humidity_2m: list[FiniteFloat | None] | None = None
    surface_pressure: list[FiniteFloat | None] | None = None
    wind_speed_10m: list[FiniteFloat | None] | None = None
    precipitation: list[FiniteFloat | None] | None = None
    shortwave_radiation: list[FiniteFloat | None] | None = None
    cloud_cover: list[FiniteFloat | None] | None = None
    sunshine_duration: list[FiniteFloat | None] | None = None

    def series(self) -> dict[str, list[float | None]]:
        """Every requested variable's values, one per hour. Open-Meteo may omit
        a variable's array entirely; that means no value for any hour, not a
        length mismatch."""
        return {
            name: _values_or_nulls(getattr(self, name), len(self.time))
            for name in HOURLY_VARIABLES
        }


class OpenMeteoDaily(BaseModel):
    time: list[date]
    temperature_2m_max: list[FiniteFloat]
    temperature_2m_mean: list[FiniteFloat | None] | None = None


class OpenMeteoForecastResponse(BaseModel):
    daily: OpenMeteoDaily


class OpenMeteoClient:
    base_url: str = "https://api.open-meteo.com/v1/forecast"

    def __init__(
        self,
        settings: LocationSettings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._clock = clock

    def _request(self, endpoint_params: dict[str, str]) -> dict[str, Any]:
        # Shared by every Open-Meteo endpoint this client calls: latitude
        # and longitude. Each caller passes its own "timezone" -- the two
        # endpoints need different ones (see each call site).
        response = requests.get(
            url=self.base_url,
            params={
                "latitude": str(self._settings.latitude),
                "longitude": str(self._settings.longitude),
                **endpoint_params,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()

    def get_recent_observations(self) -> list[WeatherObservation]:
        payload = self._request(
            {
                "hourly": HOURLY_FIELDS,
                "past_hours": str(LOOKBACK_HOURS),
                # Only the current hour's stamp, whose sums cover the hour
                # that has just ended (Open-Meteo stamps an hourly sum at the
                # end of its hour; to be confirmed against the archive when
                # the backfill is built).
                "forecast_hours": "1",
                # Forcing UTC means the naive hour stamps Open-Meteo returns
                # can be treated as UTC below without guessing an offset.
                "timezone": "UTC",
            }
        )
        hourly = OpenMeteoHourly.model_validate(payload["hourly"])
        series = hourly.series()
        mismatched = {
            name: len(values)
            for name, values in series.items()
            if len(values) != len(hourly.time)
        }
        if mismatched:
            raise ValueError(
                "Open-Meteo hourly response has mismatched array lengths: "
                f"{len(hourly.time)} time entries vs {mismatched}."
            )
        location = location_key(self._settings.latitude, self._settings.longitude)
        current_hour = self._clock().replace(minute=0, second=0, microsecond=0)

        observations = []
        for i, naive_hour in enumerate(hourly.time):
            # Open-Meteo's naive stamps are UTC because the request asked for it.
            observed_at = naive_hour.replace(tzinfo=UTC)
            if observed_at > current_hour:
                continue
            hour = {name: values[i] for name, values in series.items()}
            # An hour with no values at all carries no information to store.
            if all(value is None for value in hour.values()):
                continue
            observations.append(
                WeatherObservation(
                    source="open-meteo",
                    location=location,
                    observed_at=observed_at,
                    temp=hour["temperature_2m"],
                    humidity=hour["relative_humidity_2m"],
                    pressure=hour["surface_pressure"],
                    wind_speed=hour["wind_speed_10m"],
                    precipitation=hour["precipitation"],
                    shortwave_radiation=hour["shortwave_radiation"],
                    cloud_cover=hour["cloud_cover"],
                    sunshine_duration=hour["sunshine_duration"],
                )
            )
        return observations

    def get_forecast(self) -> list[WeatherForecastDay]:
        payload = self._request(
            {
                "daily": DAILY_FIELDS,
                # This repo buckets "day" as the Europe/London local
                # calendar day for consumption/cost data (ADR-0010), not
                # UTC -- requesting Open-Meteo's daily aggregation in that
                # same timezone keeps target_date aligned with
                # daily_consumption_summary's day boundaries, so a later
                # join (gas cost projection, #511) compares like-for-like
                # days instead of drifting by up to an hour around BST
                # transitions.
                "timezone": "Europe/London",
            }
        )
        parsed = OpenMeteoForecastResponse.model_validate(payload)
        daily = parsed.daily

        mean_temps = _values_or_nulls(daily.temperature_2m_mean, len(daily.time))

        if len(daily.time) != len(daily.temperature_2m_max) or len(daily.time) != len(
            mean_temps
        ):
            raise ValueError(
                "Open-Meteo forecast response has mismatched array lengths: "
                f"{len(daily.time)} time entries vs "
                f"{len(daily.temperature_2m_max)} temperature_2m_max entries vs "
                f"{len(mean_temps)} temperature_2m_mean entries."
            )

        fetched_at = self._clock()
        return [
            WeatherForecastDay(
                source="open-meteo",
                target_date=target_date,
                max_temp=max_temp,
                fetched_at=fetched_at,
                mean_temp=mean_temp,
            )
            for target_date, max_temp, mean_temp in zip(
                daily.time, daily.temperature_2m_max, mean_temps
            )
        ]

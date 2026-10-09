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

HOURLY_FIELDS = (
    "temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m,"
    "precipitation,shortwave_radiation,cloud_cover,sunshine_duration"
)
DAILY_FIELDS = "temperature_2m_max,temperature_2m_mean"


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
            name: (
                [None] * len(self.time)
                if getattr(self, name) is None
                else getattr(self, name)
            )
            for name in HOURLY_FIELDS.split(",")
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
                "past_hours": "24",
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

        return [
            WeatherObservation(
                source="open-meteo",
                location=location,
                observed_at=hour,
                temp=series["temperature_2m"][i],
                humidity=series["relative_humidity_2m"][i],
                pressure=series["surface_pressure"][i],
                wind_speed=series["wind_speed_10m"][i],
                precipitation=series["precipitation"][i],
                shortwave_radiation=series["shortwave_radiation"][i],
                cloud_cover=series["cloud_cover"][i],
                sunshine_duration=series["sunshine_duration"][i],
            )
            for i, naive_hour in enumerate(hourly.time)
            # Open-Meteo's naive stamps are UTC because the request asked for it.
            if (hour := naive_hour.replace(tzinfo=UTC)) <= current_hour
            # An hour with no values at all carries no information to store.
            and any(values[i] is not None for values in series.values())
        ]

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

        mean_temps = daily.temperature_2m_mean
        if mean_temps is None:
            mean_temps = [None] * len(daily.time)

        if len(daily.time) != len(daily.temperature_2m_max) or len(daily.time) != len(
            mean_temps
        ):
            raise ValueError(
                "Open-Meteo forecast response has mismatched array lengths: "
                f"{len(daily.time)} time entries vs "
                f"{len(daily.temperature_2m_max)} temperature_2m_max entries vs "
                f"{len(mean_temps)} temperature_2m_mean entries."
            )

        fetched_at = datetime.now(UTC)
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

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

CURRENT_FIELDS = (
    "temperature_2m,relative_humidity_2m,surface_pressure,wind_speed_10m,"
    "precipitation,shortwave_radiation,cloud_cover,sunshine_duration"
)
DAILY_FIELDS = "temperature_2m_max,temperature_2m_mean"


class OpenMeteoCurrent(BaseModel):
    time: datetime
    temperature_2m: FiniteFloat
    relative_humidity_2m: FiniteFloat
    surface_pressure: FiniteFloat
    wind_speed_10m: FiniteFloat
    precipitation: FiniteFloat
    shortwave_radiation: FiniteFloat | None = None
    cloud_cover: FiniteFloat | None = None
    sunshine_duration: FiniteFloat | None = None


class OpenMeteoResponse(BaseModel):
    current: OpenMeteoCurrent


class OpenMeteoDaily(BaseModel):
    time: list[date]
    temperature_2m_max: list[FiniteFloat]
    temperature_2m_mean: list[FiniteFloat | None] | None = None


class OpenMeteoForecastResponse(BaseModel):
    daily: OpenMeteoDaily


class OpenMeteoClient:
    base_url: str = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, settings: LocationSettings) -> None:
        self._settings = settings

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

    def get_current_observation(self) -> WeatherObservation:
        payload = self._request(
            {
                "current": CURRENT_FIELDS,
                # Open-Meteo defaults "current.time" to the location's local
                # timezone (or GMT) when none is requested -- forcing UTC
                # here means the naive timestamp it returns can be treated
                # as UTC below without guessing an offset.
                "timezone": "UTC",
            }
        )
        parsed = OpenMeteoResponse.model_validate(payload)
        current = parsed.current

        return WeatherObservation(
            source="open-meteo",
            location=location_key(self._settings.latitude, self._settings.longitude),
            observed_at=current.time.replace(tzinfo=UTC),
            temp=current.temperature_2m,
            humidity=current.relative_humidity_2m,
            pressure=current.surface_pressure,
            wind_speed=current.wind_speed_10m,
            precipitation=current.precipitation,
            shortwave_radiation=current.shortwave_radiation,
            cloud_cover=current.cloud_cover,
            sunshine_duration=current.sunshine_duration,
        )

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

        if not len(daily.time) == len(daily.temperature_2m_max) == len(mean_temps):
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

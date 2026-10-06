from datetime import datetime

import requests
from pydantic import BaseModel

from hive_app.common.config import WeatherUndergroundSettings
from hive_app.data.model import WeatherObservation
from hive_app.data.weather_types import REQUEST_TIMEOUT_SECONDS, FiniteFloat


class WeatherUndergroundMetric(BaseModel):
    temp: FiniteFloat
    pressure: FiniteFloat
    windSpeed: FiniteFloat
    precipTotal: FiniteFloat


class WeatherUndergroundObservation(BaseModel):
    obsTimeUtc: datetime
    humidity: FiniteFloat
    metric: WeatherUndergroundMetric


class WeatherUndergroundResponse(BaseModel):
    observations: list[WeatherUndergroundObservation]


class WeatherUndergroundNearbyLocation(BaseModel):
    stationId: list[str]
    distanceKm: list[FiniteFloat]


class WeatherUndergroundNearbyResponse(BaseModel):
    location: WeatherUndergroundNearbyLocation


class WeatherStationNotReportingError(RuntimeError):
    """The station answered but returned no reading (it may be offline)."""


class WeatherUndergroundClient:
    base_url: str = "https://api.weather.com/v2/pws/observations/current"
    nearby_url: str = "https://api.weather.com/v3/location/near"

    def __init__(self, settings: WeatherUndergroundSettings) -> None:
        self._settings = settings

    def find_nearby_stations(self, latitude: float, longitude: float) -> list[str]:
        """Station ids near the point, nearest first."""
        response = self._get(
            self.nearby_url,
            {
                "geocode": f"{latitude},{longitude}",
                "product": "pws",
                "format": "json",
                "apiKey": self._settings.api_key,
            },
        )
        location = WeatherUndergroundNearbyResponse.model_validate(
            response.json()
        ).location
        by_distance = sorted(
            zip(location.distanceKm, location.stationId), key=lambda pair: pair[0]
        )
        return [station_id for _, station_id in by_distance]

    def get_current_observation(
        self, station_id: str | None = None
    ) -> WeatherObservation:
        """Current reading for `station_id`, defaulting to the configured
        station. Raises WeatherStationNotReportingError when the station
        has no reading."""
        if station_id is None:
            station_id = self._settings.station_id
        if station_id is None:
            raise RuntimeError("No Weather Underground station to read.")
        response = self._get(
            self.base_url,
            {
                "stationId": station_id,
                "format": "json",
                "units": "m",
                "apiKey": self._settings.api_key,
            },
        )
        parsed = WeatherUndergroundResponse.model_validate(response.json())
        if not parsed.observations:
            raise WeatherStationNotReportingError(
                f"Weather Underground returned no observations for station "
                f"{station_id!r} (station may be offline)."
            )
        observation = parsed.observations[0]

        return WeatherObservation(
            source="wunderground",
            observed_at=observation.obsTimeUtc,
            temp=observation.metric.temp,
            humidity=observation.humidity,
            pressure=observation.metric.pressure,
            wind_speed=observation.metric.windSpeed,
            precipitation=observation.metric.precipTotal,
        )

    def _get(self, url: str, params: dict[str, str]) -> requests.Response:
        try:
            response = requests.get(
                url=url,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            # requests.RequestException's message -- and, for an HTTPError
            # specifically, its .response.url -- both embed the full request
            # URL, including the apiKey query parameter above. This covers
            # every failure requests.get()/raise_for_status() can raise
            # (connection errors, timeouts, HTTP error statuses), not just
            # HTTPError, since a ConnectionError's message also carries the
            # prepared URL. WU has no header-based auth alternative, so
            # letting any of these propagate (and later be logged with
            # exc_info=True by WeatherRetriever.refresh()'s failure path)
            # would write the API key to application logs. Re-raised with
            # only the status code where one exists, `from None` to
            # suppress the original exception's chain so the
            # credential-bearing URL never reaches a log line.
            status = (
                e.response.status_code
                if isinstance(e, requests.HTTPError) and e.response is not None
                else "unknown"
            )
            raise RuntimeError(
                f"Weather Underground request failed with status {status}."
            ) from None
        return response

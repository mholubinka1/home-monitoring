import logging.config
from logging import Logger, getLogger

from hive_app.common.config import WeatherUndergroundSettings
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.model import WeatherForecastDay, WeatherObservation
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.open_meteo_client import OpenMeteoClient
from hive_app.data.weather_location import WeatherLocationResolver
from hive_app.data.weather_underground_client import WeatherUndergroundClient

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

# How many of the nearest stations are tried for a current reading.
MAX_STATION_CANDIDATES = 5


class WeatherApiSource:
    _wunderground: WeatherUndergroundClient | None
    _locations: WeatherLocationResolver
    _mariadb: MariaDBClient

    def __init__(
        self,
        wunderground: WeatherUndergroundSettings | None,
        locations: WeatherLocationResolver,
        mariadb: MariaDBClient,
    ) -> None:
        self._wunderground = (
            WeatherUndergroundClient(wunderground) if wunderground else None
        )
        self._explicit_station = wunderground.station_id if wunderground else None
        self._locations = locations
        self._mariadb = mariadb

    def _open_meteo(self) -> OpenMeteoClient:
        return OpenMeteoClient(self._locations.resolve())

    def fetch_current_observation(self) -> WeatherObservation:
        if self._wunderground is None:
            # No Weather Underground station configured: Open-Meteo is the
            # only observation source.
            return self._open_meteo().get_current_observation()
        if self._explicit_station is not None:
            # Explicit station config always wins: no discovery, no caching.
            return self._wunderground.get_current_observation()
        cached = self._mariadb.read_weather_station()
        if cached is None:
            return self._discover_station(self._wunderground)
        try:
            return self._wunderground.get_current_observation(cached)
        except Exception:
            # Cleared so the next run re-picks; this run falls through to
            # Open-Meteo via WeatherRetriever.
            self._mariadb.write_weather_station(None)
            raise

    def _discover_station(
        self, wunderground: WeatherUndergroundClient
    ) -> WeatherObservation:
        """The reading from the nearest station that has one, caching that
        station."""
        location = self._locations.resolve()
        stations = wunderground.find_nearby_stations(
            location.latitude, location.longitude
        )
        for station_id in stations[:MAX_STATION_CANDIDATES]:
            try:
                observation = wunderground.get_current_observation(station_id)
            except RuntimeError:
                continue
            self._mariadb.write_weather_station(station_id)
            logger.info(f"Weather Underground station {station_id} picked.")
            return observation
        raise RuntimeError("No nearby Weather Underground station is reporting.")

    def fetch_current_observation_fallback(self) -> WeatherObservation | None:
        if self._wunderground is None:
            # Open-Meteo was already the primary source; retrying it is no
            # fallback.
            return None
        return self._open_meteo().get_current_observation()

    def persist_current_observation(self, observation: WeatherObservation) -> None:
        self._mariadb.write_weather_observation(observation)

    def fetch_forecast(self) -> list[WeatherForecastDay]:
        return self._open_meteo().get_forecast()

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        self._mariadb.write_weather_forecast(forecast)

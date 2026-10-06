from hive_app.common.config import WeatherUndergroundSettings
from hive_app.data.model import WeatherForecastDay, WeatherObservation
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.open_meteo_client import OpenMeteoClient
from hive_app.data.weather_location import WeatherLocationResolver
from hive_app.data.weather_underground_client import WeatherUndergroundClient


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
        self._locations = locations
        self._mariadb = mariadb

    def _open_meteo(self) -> OpenMeteoClient:
        return OpenMeteoClient(self._locations.resolve())

    def fetch_current_observation(self) -> WeatherObservation:
        if self._wunderground is None:
            # No Weather Underground station configured: Open-Meteo is the
            # only observation source.
            return self._open_meteo().get_current_observation()
        return self._wunderground.get_current_observation()

    def fetch_current_observation_fallback(self) -> WeatherObservation:
        return self._open_meteo().get_current_observation()

    def persist_current_observation(self, observation: WeatherObservation) -> None:
        self._mariadb.write_weather_observation(observation)

    def fetch_forecast(self) -> list[WeatherForecastDay]:
        return self._open_meteo().get_forecast()

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        self._mariadb.write_weather_forecast(forecast)

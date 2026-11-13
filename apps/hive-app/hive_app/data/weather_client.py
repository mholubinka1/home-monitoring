from hive_app.data.model import (
    WeatherForecastDay,
    WeatherForecastHour,
    WeatherObservation,
)
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.open_meteo_client import OpenMeteoClient
from hive_app.data.weather_location import WeatherLocationResolver


class WeatherApiSource:
    _locations: WeatherLocationResolver
    _mariadb: MariaDBClient

    def __init__(
        self, locations: WeatherLocationResolver, mariadb: MariaDBClient
    ) -> None:
        self._locations = locations
        self._mariadb = mariadb

    def _open_meteo(self) -> OpenMeteoClient:
        return OpenMeteoClient(self._locations.resolve())

    def fetch_recent_observations(self) -> list[WeatherObservation]:
        return self._open_meteo().get_recent_observations()

    def persist_observations(self, observations: list[WeatherObservation]) -> None:
        self._mariadb.write_weather_observations(observations)

    def fetch_forecast(self) -> list[WeatherForecastDay]:
        return self._open_meteo().get_forecast()

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None:
        self._mariadb.write_weather_forecast(forecast)

    def fetch_hourly_forecast(self) -> list[WeatherForecastHour]:
        return self._open_meteo().get_hourly_forecast()

    def persist_hourly_forecast(self, hours: list[WeatherForecastHour]) -> None:
        self._mariadb.write_weather_forecast_hours(hours)

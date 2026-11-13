import logging.config
from logging import Logger, getLogger
from typing import Protocol

from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.model import (
    WeatherForecastDay,
    WeatherForecastHour,
    WeatherObservation,
)

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)


class WeatherSource(Protocol):
    def fetch_recent_observations(self) -> list[WeatherObservation]: ...

    def persist_observations(self, observations: list[WeatherObservation]) -> None: ...

    def fetch_forecast(self) -> list[WeatherForecastDay]: ...

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None: ...

    def fetch_hourly_forecast(self) -> list[WeatherForecastHour]: ...

    def persist_hourly_forecast(self, hours: list[WeatherForecastHour]) -> None: ...


class WeatherRetriever:
    _client: WeatherSource

    def __init__(self, client: WeatherSource) -> None:
        self._client = client

    def refresh(self) -> None:
        # No try/except: Open-Meteo is the only observation source, so any
        # failure must propagate untouched to the generic job wrapper, which
        # handles retry-with-backoff and job_run failure recording.
        observations = self._client.fetch_recent_observations()
        self._client.persist_observations(observations)
        if not observations:
            # The job still succeeds; this line is the only trace that
            # Open-Meteo gave back nothing storable.
            logger.warning("Weather observation refresh: no hours to store.")
            return
        logger.info(
            f"Weather observation refresh: persisted {len(observations)} hour(s)."
        )

    def refresh_forecast(self) -> None:
        # No try/except, as in refresh(): any failure propagates untouched to
        # the generic job wrapper.
        forecast = self._client.fetch_forecast()
        self._client.persist_forecast(forecast)
        logger.info(f"Weather forecast refresh: persisted {len(forecast)} day(s).")
        hours = self._client.fetch_hourly_forecast()
        self._client.persist_hourly_forecast(hours)
        logger.info(f"Weather forecast refresh: persisted {len(hours)} hour(s).")

import logging.config
from logging import Logger, getLogger
from typing import Protocol

from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.model import WeatherForecastDay, WeatherObservation

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)


class WeatherSource(Protocol):
    def fetch_current_observation(self) -> WeatherObservation: ...

    def fetch_current_observation_fallback(self) -> WeatherObservation | None:
        """None when the primary fetch already was the last-resort source."""
        ...

    def persist_current_observation(self, observation: WeatherObservation) -> None: ...

    def fetch_forecast(self) -> list[WeatherForecastDay]: ...

    def persist_forecast(self, forecast: list[WeatherForecastDay]) -> None: ...


class WeatherRetriever:
    _client: WeatherSource

    def __init__(self, client: WeatherSource) -> None:
        self._client = client

    def refresh(self) -> None:
        # Mutual exclusivity is load-bearing, not incidental: the fallback
        # fetch must only ever be reachable via the primary fetch's except
        # branch, never called unconditionally alongside it. Mirrors
        # AgileForecastRetriever.refresh()'s primary/fallback shape.
        try:
            observation = self._client.fetch_current_observation()
        except Exception as primary_error:
            try:
                fallback = self._client.fetch_current_observation_fallback()
            except Exception:
                # The fallback's own error propagates, but must not bury why
                # the primary failed.
                logger.warning(
                    "Primary weather source failed; Open-Meteo fallback also failed.",
                    exc_info=primary_error,
                )
                raise
            if fallback is None:
                raise
            logger.warning(
                "Primary weather source failed; falling back to Open-Meteo.",
                exc_info=primary_error,
            )
            observation = fallback

        self._client.persist_current_observation(observation)
        logger.info(
            f"Weather observation refresh: persisted from {observation.source}."
        )

    def refresh_forecast(self) -> None:
        # No try/except here, unlike refresh(): Open-Meteo is the only
        # forecast source (AC2 -- no fallback source attempted), so any
        # failure must propagate untouched to the generic job wrapper,
        # which handles retry-with-backoff and job_run failure recording.
        forecast = self._client.fetch_forecast()
        self._client.persist_forecast(forecast)
        logger.info(f"Weather forecast refresh: persisted {len(forecast)} day(s).")

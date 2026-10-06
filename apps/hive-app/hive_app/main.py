import argparse
import logging.config
import sys
import threading
import time
from collections.abc import Callable
from logging import Logger, getLogger

from schedule import Job, Scheduler, default_scheduler

from hive_app.common.config import (
    LocationSettings,
    get_settings,
)
from hive_app.common.decorator import retry_with_exponential_backoff
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.auth import HiveAuthenticator
from hive_app.data.heating import HeatingRetriever
from hive_app.data.hive_client import HiveApiSource
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.notify import NtfyReauthNotifier, ReauthAlert, ReauthNotifier
from hive_app.data.weather import WeatherRetriever
from hive_app.data.weather_client import WeatherApiSource
from hive_app.data.weather_location import WeatherLocationResolver

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

HEATING_REFRESH_JOB = "heating_refresh"
HEATING_REFRESH_INTERVAL_SECONDS = 120
WEATHER_OBSERVATION_REFRESH_JOB = "weather_observation_refresh"
WEATHER_OBSERVATION_REFRESH_INTERVAL_MINUTES = 60
WEATHER_FORECAST_REFRESH_JOB = "weather_forecast_refresh"
WEATHER_FORECAST_REFRESH_INTERVAL_MINUTES = 60


def _with_backoff_recording(
    job_name: str,
    refresh_fn: Callable[[], None],
    mariadb: MariaDBClient,
) -> Callable[[], None]:
    """Returns a blocking callable that runs refresh_fn with retry-with-backoff,
    recording the outcome as a job_run. Never raises -- retry_with_exponential_backoff
    swallows the final failure after exhausting its attempts, so callers must check
    the recorded job_run rows to learn the outcome, not exception handling."""

    @retry_with_exponential_backoff()
    def attempt_with_backoff() -> None:
        try:
            refresh_fn()
            mariadb.record_job_run(job_name, "success")
        except Exception as e:
            mariadb.record_job_run(job_name, "failure", error=str(e))
            raise RuntimeError(f"{job_name} failed: {e}") from e

    return attempt_with_backoff


def _run_in_background(
    job_name: str, attempt_fn: Callable[[], None]
) -> Callable[[], threading.Thread]:
    """Returns a callable that starts (or reuses) a background worker thread
    running attempt_fn. Skips starting a new worker if one is already running."""
    worker: threading.Thread | None = None

    def run() -> threading.Thread:
        nonlocal worker
        if worker is not None and worker.is_alive():
            logger.info(f"{job_name} is still running; skipping this invocation.")
            return worker
        worker = threading.Thread(target=attempt_fn, daemon=True)
        worker.start()
        return worker

    return run


def _run_with_backoff_in_background(
    job_name: str,
    refresh_fn: Callable[[], None],
    mariadb: MariaDBClient,
) -> Callable[[], threading.Thread]:
    """Returns a callable that starts (or reuses) a background worker thread
    running refresh_fn with retry-with-backoff, recording the outcome as a
    job_run. Skips starting a new worker if one is already running."""
    return _run_in_background(
        job_name, _with_backoff_recording(job_name, refresh_fn, mariadb)
    )


def _schedule_refresh_job(
    scheduler: Scheduler,
    schedule_interval: Callable[[Scheduler], Job],
    job_name: str,
    refresh_fn: Callable[[], None],
    mariadb: MariaDBClient,
) -> Job:
    refresh = _run_with_backoff_in_background(job_name, refresh_fn, mariadb)
    return schedule_interval(scheduler).do(refresh)


def register_heating_refresh_job(
    scheduler: Scheduler,
    heating: HeatingRetriever,
    mariadb: MariaDBClient,
) -> Job:
    return _schedule_refresh_job(
        scheduler,
        lambda s: s.every(HEATING_REFRESH_INTERVAL_SECONDS).seconds,
        HEATING_REFRESH_JOB,
        heating.refresh,
        mariadb,
    )


def register_weather_observation_refresh_job(
    scheduler: Scheduler,
    weather: WeatherRetriever,
    mariadb: MariaDBClient,
) -> Job:
    return _schedule_refresh_job(
        scheduler,
        lambda s: s.every(WEATHER_OBSERVATION_REFRESH_INTERVAL_MINUTES).minutes,
        WEATHER_OBSERVATION_REFRESH_JOB,
        weather.refresh,
        mariadb,
    )


def register_weather_forecast_refresh_job(
    scheduler: Scheduler,
    weather: WeatherRetriever,
    mariadb: MariaDBClient,
) -> Job:
    return _schedule_refresh_job(
        scheduler,
        lambda s: s.every(WEATHER_FORECAST_REFRESH_INTERVAL_MINUTES).minutes,
        WEATHER_FORECAST_REFRESH_JOB,
        weather.refresh_forecast,
        mariadb,
    )


def register_weather_jobs(
    scheduler: Scheduler,
    weather: WeatherRetriever,
    mariadb: MariaDBClient,
) -> tuple[Job, Job]:
    # Always registered, whatever weather config is present: the Weather
    # Location is derived lazily on each run (ADR-0028).
    return (
        register_weather_observation_refresh_job(scheduler, weather, mariadb),
        register_weather_forecast_refresh_job(scheduler, weather, mariadb),
    )


def run_pending_safely(scheduler: Scheduler) -> None:
    try:
        scheduler.run_pending()
    except Exception:
        logger.exception("Unhandled error while running scheduled jobs.")


def authenticate_at_startup(authenticator: HiveAuthenticator) -> None:
    try:
        authenticator.authenticate()
    except Exception:
        logger.critical(
            "Hive authentication failed at startup; heating_refresh will keep "
            "retrying against whatever auth state is persisted.",
            exc_info=True,
        )


def _build_reauth_notifier(topic_url: str | None) -> ReauthNotifier | None:
    if not topic_url:
        logger.warning(
            "ntfy is not configured; hive-app will not send a live alert if "
            "Hive re-authentication is required (see job_run for failures)."
        )
        return None
    return NtfyReauthNotifier(topic_url)


def _build_reauth_alert(topic_url: str | None) -> ReauthAlert:
    return ReauthAlert(_build_reauth_notifier(topic_url))


def _build_weather_retriever(
    location: LocationSettings | None,
    mariadb: MariaDBClient,
) -> WeatherRetriever:
    locations = WeatherLocationResolver(location, mariadb)
    weather_source = WeatherApiSource(locations, mariadb)
    return WeatherRetriever(weather_source)


def main() -> None:
    logger.info("Starting hive-app.")

    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--config-file")
        args = parser.parse_args()
        settings = get_settings(config_file_path=args.config_file)
        logger.info("Startup complete.")
    except Exception as e:
        logger.critical(f"Error loading startup configurations: {e}.")
        sys.exit(1)

    mariadb = MariaDBClient(settings.mariadb)
    hive_source = HiveApiSource(settings.hive, mariadb)
    reauth_alert = _build_reauth_alert(
        settings.ntfy.topic_url if settings.ntfy else None
    )
    heating = HeatingRetriever(hive_source, reauth_alert)
    authenticator = HiveAuthenticator(hive_source, reauth_alert)

    authenticate_at_startup(authenticator)
    register_heating_refresh_job(default_scheduler, heating, mariadb)

    weather = _build_weather_retriever(settings.location, mariadb)
    register_weather_jobs(default_scheduler, weather, mariadb)

    while True:
        run_pending_safely(default_scheduler)
        time.sleep(1)


if __name__ == "__main__":
    main()

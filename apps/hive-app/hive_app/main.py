import argparse
import logging.config
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
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
from hive_app.data.weather_types import location_key
from hive_app.weather_backfill import (
    DEFAULT_START,
    backfill_and_log,
    describe_error,
    history_start,
)

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

HEATING_REFRESH_JOB = "heating_refresh"
HEATING_REFRESH_INTERVAL_SECONDS = 120
WEATHER_OBSERVATION_REFRESH_JOB = "weather_observation_refresh"
WEATHER_OBSERVATION_REFRESH_INTERVAL_MINUTES = 60
WEATHER_FORECAST_REFRESH_JOB = "weather_forecast_refresh"
WEATHER_FORECAST_REFRESH_INTERVAL_MINUTES = 60
WEATHER_HISTORY_BACKFILL_JOB = "weather_history_backfill"


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


def run_weather_backfill_at_startup(
    mariadb: MariaDBClient,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> threading.Thread | None:
    # Gated on the data, not on a prior job_run: the backfill runs whenever the
    # stored history for the current location doesn't reach back to the first
    # hour of DEFAULT_START.
    try:
        location = mariadb.read_weather_location()
        oldest = (
            mariadb.oldest_weather_observation(
                location_key(location.latitude, location.longitude)
            )
            if location is not None
            else None
        )
    except Exception as e:
        # An optional background task must not stop hive-app starting; the
        # error's text may carry SQL and values, so only describe it.
        logger.warning(
            f"Could not check stored weather history ({describe_error(e)}); "
            "skipping weather history backfill until the next restart."
        )
        return None
    if location is None:
        logger.info(
            "No weather location is cached yet; the next restart will backfill "
            "weather history once a location is resolved."
        )
        return None
    if oldest is not None and oldest <= history_start():
        logger.info(
            f"Stored weather starts at or before {DEFAULT_START}; "
            "skipping weather history backfill."
        )
        return None
    if oldest is None:
        logger.info(
            "No stored weather for the current location; "
            f"backfilling history from {DEFAULT_START}."
        )
    else:
        logger.info(
            f"Stored weather doesn't reach back to {DEFAULT_START}; "
            "backfilling history."
        )
    return _run_with_backoff_in_background(
        WEATHER_HISTORY_BACKFILL_JOB,
        lambda: backfill_and_log(mariadb, clock),
        mariadb,
    )()


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
    run_weather_backfill_at_startup(mariadb)

    while True:
        run_pending_safely(default_scheduler)
        time.sleep(1)


if __name__ == "__main__":
    main()

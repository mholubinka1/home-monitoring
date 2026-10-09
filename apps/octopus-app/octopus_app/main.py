import argparse
import datetime
import logging.config
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime as dt
from datetime import timedelta
from logging import Logger, getLogger

from schedule import Job, Scheduler, default_scheduler

from octopus_app.common.config import RefreshSettings, get_settings
from octopus_app.common.decorator import retry_with_exponential_backoff
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import local_day
from octopus_app.data.agile_forecast import AgileForecastRetriever
from octopus_app.data.base import MonitoringClient
from octopus_app.data.consumption import ConsumptionRetriever
from octopus_app.data.consumption_summary import (
    ConsumptionSummaryBackfill,
    ConsumptionSummaryRetriever,
)
from octopus_app.data.cost_forecast import CostForecastRetriever
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.pricing import PricingRetriever
from octopus_app.data.pruning import DataPruner

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

CONSUMPTION_REFRESH_JOB = "consumption_refresh"
CONSUMPTION_BACKFILL_JOB = "consumption_backfill"
PRICING_REFRESH_JOB = "pricing_refresh"
WEEKLY_CONSUMPTION_SUMMARY_JOB = "update_consumption_summary"
PRUNE_OLD_DATA_JOB = "prune_old_data"
YEARLY_COMPARISON_BACKFILL_JOB = "yearly_comparison_backfill"
COST_FORECAST_REFRESH_JOB = "cost_forecast_refresh"
AGILE_FORECAST_REFRESH_JOB = "agile_forecast_refresh"
MIN_COUNTED_HISTORY_DAYS = 183  # roughly 6 months
DAILY_JOB_TIME = "04:00"  # shared by every daily/weekly-cadence job, so
# none of them land in watchtower's 03:00 update window -- that schedule is
# configured on the Pi host (pi-desktop's compose stack), not in this repo's
# docker-compose.yml, which only enables watchtower via container labels.


def _retention_window_start(retention_days: int) -> dt:
    current_time = dt.now(datetime.UTC)
    limit = (current_time - timedelta(days=retention_days)).date()
    return dt(limit.year, limit.month, limit.day, tzinfo=datetime.UTC)


def _retrieve_full_window(
    label: str,
    consumption: ConsumptionRetriever,
    refresh_config: RefreshSettings,
) -> None:
    limit_dt = _retention_window_start(refresh_config.retention)
    logger.info(f"{label} Retrieving consumption history from {limit_dt}.")
    consumption.retrieve(period_from=limit_dt)


def startup(
    consumption: ConsumptionRetriever,
    refresh_config: RefreshSettings,
) -> None:
    _retrieve_full_window("Startup.", consumption, refresh_config)


def run_initial_pricing_sync(pricing: PricingRetriever) -> None:
    try:
        pricing.refresh()
    except Exception:
        logger.exception("Pricing sync failed at startup; continuing.")


def run_initial_agile_forecast_sync(agile_forecast: AgileForecastRetriever) -> None:
    try:
        agile_forecast.refresh()
    except Exception:
        logger.exception("Agile forecast sync failed at startup; continuing.")


def run_initial_consumption_summary_sync(
    consumption_summary: ConsumptionSummaryRetriever,
) -> None:
    try:
        consumption_summary.refresh()
    except Exception:
        logger.exception("Consumption summary sync failed at startup; continuing.")


def run_initial_cost_forecast_sync(cost_forecast: CostForecastRetriever) -> None:
    try:
        cost_forecast.refresh()
    except Exception:
        logger.exception("Cost forecast sync failed at startup; continuing.")


def _with_backoff_recording(
    job_name: str,
    refresh_fn: Callable[[], None],
    mariadb: MariaDBClient,
) -> Callable[[], None]:
    """Returns a blocking callable that runs refresh_fn with retry-with-backoff,
    recording the outcome as a job_run. Never raises -- retry_with_exponential_backoff
    swallows the final failure after exhausting its attempts, so callers must check
    the recorded job_run (e.g. via mariadb.latest_job_run_is_successful) to learn the
    outcome, not exception handling."""

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


def run_backfill_at_startup(
    backfill: ConsumptionSummaryBackfill,
    mariadb: MariaDBClient,
    as_of: dt | None = None,
) -> threading.Thread | None:
    # Gated on the data, not on a prior job_run: the backfill runs whenever the
    # summary holds less than MIN_COUNTED_HISTORY_DAYS of counted days.
    if as_of is None:
        as_of = dt.now(datetime.UTC)
    threshold = local_day.to_local_date(as_of) - timedelta(
        days=MIN_COUNTED_HISTORY_DAYS
    )
    oldest_counted = mariadb.oldest_counted_summary_date()
    if oldest_counted is not None and oldest_counted <= threshold:
        logger.info(
            "Consumption summary already holds at least 6 months of counted days; "
            "skipping yearly comparison backfill."
        )
        return None
    run = _run_with_backoff_in_background(
        YEARLY_COMPARISON_BACKFILL_JOB, backfill.run, mariadb
    )
    return run()


def register_jobs(
    scheduler: Scheduler,
    refresh_config: RefreshSettings,
    consumption: ConsumptionRetriever,
    mariadb: MariaDBClient,
) -> Job:
    return _schedule_refresh_job(
        scheduler,
        lambda s: s.every(refresh_config.refresh_interval).hours,
        CONSUMPTION_REFRESH_JOB,
        consumption.refresh,
        mariadb,
    )


def register_consumption_backfill_job(
    scheduler: Scheduler,
    refresh_config: RefreshSettings,
    consumption: ConsumptionRetriever,
    mariadb: MariaDBClient,
) -> Job:
    def backfill() -> None:
        _retrieve_full_window("Consumption backfill.", consumption, refresh_config)

    return _schedule_refresh_job(
        scheduler,
        lambda s: s.every().day.at(DAILY_JOB_TIME),
        CONSUMPTION_BACKFILL_JOB,
        backfill,
        mariadb,
    )


def register_pricing_job(
    scheduler: Scheduler,
    refresh_config: RefreshSettings,
    pricing: PricingRetriever,
    mariadb: MariaDBClient,
) -> Job:
    return _schedule_refresh_job(
        scheduler,
        lambda s: s.every(refresh_config.refresh_interval).hours,
        PRICING_REFRESH_JOB,
        pricing.refresh,
        mariadb,
    )


def register_consumption_summary_job(
    scheduler: Scheduler,
    consumption_summary: ConsumptionSummaryRetriever,
    pruner: DataPruner,
    mariadb: MariaDBClient,
) -> Job:
    """Registers one weekly Monday-04:00 job that summarizes, then -- only if that
    summarization succeeded -- prunes raw data in the same background thread,
    immediately after. Sequencing them within one thread (rather than as two
    independently-scheduled jobs) is deliberate: prune_old_data's job_run gate must
    see *this* cycle's summarization outcome, not whichever outcome happened to be
    most recently recorded when its own tick fired -- two separately-scheduled jobs
    can't guarantee that ordering, since the summary job's own retry-with-backoff
    dispatch is itself asynchronous."""
    summarize = _with_backoff_recording(
        WEEKLY_CONSUMPTION_SUMMARY_JOB, consumption_summary.refresh, mariadb
    )
    prune = _with_backoff_recording(PRUNE_OLD_DATA_JOB, pruner.run, mariadb)

    def summarize_then_prune() -> None:
        summarize()
        if mariadb.latest_job_run_is_successful(WEEKLY_CONSUMPTION_SUMMARY_JOB):
            prune()
        else:
            logger.info(
                f"{WEEKLY_CONSUMPTION_SUMMARY_JOB} did not succeed this cycle; "
                f"skipping {PRUNE_OLD_DATA_JOB}."
            )
            mariadb.record_job_run(PRUNE_OLD_DATA_JOB, "skipped")

    run = _run_in_background(WEEKLY_CONSUMPTION_SUMMARY_JOB, summarize_then_prune)
    return scheduler.every().monday.at(DAILY_JOB_TIME).do(run)


def register_cost_forecast_refresh_job(
    scheduler: Scheduler,
    cost_forecast: CostForecastRetriever,
    mariadb: MariaDBClient,
) -> Job:
    return _schedule_refresh_job(
        scheduler,
        lambda s: s.every(1).hours,
        COST_FORECAST_REFRESH_JOB,
        cost_forecast.refresh,
        mariadb,
    )


def register_agile_forecast_refresh_job(
    scheduler: Scheduler,
    agile_forecast: AgileForecastRetriever,
    mariadb: MariaDBClient,
) -> Job:
    return _schedule_refresh_job(
        scheduler,
        lambda s: s.every(1).hours,
        AGILE_FORECAST_REFRESH_JOB,
        agile_forecast.refresh,
        mariadb,
    )


def run_pending_safely(scheduler: Scheduler) -> None:
    try:
        scheduler.run_pending()
    except Exception:
        logger.exception("Unhandled error while running scheduled jobs.")


def main() -> None:
    logger.info("Starting octopus-app.")

    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--config-file")
        args = parser.parse_args()
        settings = get_settings(config_file_path=args.config_file)
        refresh_config = settings.refresh_settings
        logger.info("Startup complete.")
    except Exception as e:
        logger.critical(f"Error loading startup configurations: {e}.")
        sys.exit(1)

    logger.info(f"Consumption data update interval {refresh_config.refresh_interval}.")

    client = MonitoringClient(settings)
    consumption = ConsumptionRetriever(client)
    pricing = PricingRetriever(client)
    consumption_summary = ConsumptionSummaryRetriever(client.mariadb)
    yearly_comparison_backfill = ConsumptionSummaryBackfill(client)
    agile_forecast = AgileForecastRetriever(client)
    cost_forecast = CostForecastRetriever(client)
    pruner = DataPruner(client.mariadb, refresh_config.retention)

    startup(consumption, refresh_config)
    run_initial_pricing_sync(pricing)
    # The backfill (background thread) and this eager sync (foreground) can
    # both upsert the same recent (energy, date) row concurrently on first
    # startup. Harmless: both compute the same correct total from the same
    # underlying consumption rows, so whichever writes last is still right.
    run_backfill_at_startup(yearly_comparison_backfill, client.mariadb)
    run_initial_consumption_summary_sync(consumption_summary)
    # Primes agile_forecast before the first cost projection ever runs, so a
    # fresh install doesn't compute an Agile customer's projection against
    # an empty table -- cost_forecast reads agile_forecast rather than
    # fetching it live (see register_agile_forecast_refresh_job below).
    run_initial_agile_forecast_sync(agile_forecast)
    run_initial_cost_forecast_sync(cost_forecast)
    register_jobs(default_scheduler, refresh_config, consumption, client.mariadb)
    # consumption_backfill shares `consumption`'s in-memory cursor with
    # consumption_refresh above, so their background threads can genuinely
    # overlap. Left unguarded, same reasoning as the backfill/eager-sync race
    # above: both compute a correct cursor from idempotent upserts, so
    # whichever finishes last is still right. See ADR-0011.
    register_consumption_backfill_job(
        default_scheduler, refresh_config, consumption, client.mariadb
    )
    register_pricing_job(default_scheduler, refresh_config, pricing, client.mariadb)
    register_consumption_summary_job(
        default_scheduler, consumption_summary, pruner, client.mariadb
    )
    register_agile_forecast_refresh_job(
        default_scheduler, agile_forecast, client.mariadb
    )
    register_cost_forecast_refresh_job(default_scheduler, cost_forecast, client.mariadb)

    while True:
        run_pending_safely(default_scheduler)
        time.sleep(1)


if __name__ == "__main__":
    main()

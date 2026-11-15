"""Fits the thermal model once a local day and stores each fit (ADR-0035,
ADR-0036)."""

import logging.config
from datetime import UTC, date, datetime, timedelta
from logging import Logger, getLogger

from common import local_day
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import thermal
from octopus_app.data.model import ThermalModelRecord
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.thermal import ThermalFit

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

# The newest room reading a step's end can use comes this long after the end.
READING_MARGIN = thermal.READING_TOLERANCE + timedelta(minutes=2)


class ThermalModelJob:
    def __init__(self, mariadb: MariaDBClient) -> None:
        self._mariadb = mariadb

    def refresh(self, now: datetime | None = None) -> None:
        """Fits the model over the 10-minute steps of the 60 days before now and
        stores one row, usable or not. Does nothing when a 10-minute fit was
        already made today, or when hive-app has stored no hourly weather or no
        thermostat readings to learn from. A model fitted on any other step is
        refitted at once."""
        now = (now or datetime.now(UTC)).astimezone(UTC)
        if self._fitted_today(now):
            return
        end = now.replace(minute=0, second=0, microsecond=0, tzinfo=None)
        start = end - thermal.FIT_WINDOW
        days = _backtest_days(now)
        first = min(start, days[0].start - thermal.FIT_WINDOW)
        # The weather first: it is the small read, so a missing hive-app costs
        # nothing more.
        weather = self._mariadb.read_hourly_weather(first, end)
        if not weather:
            logger.info("Thermal model: no hourly weather, nothing fitted.")
            return
        history = timedelta(minutes=max(thermal.SEARCHED_DELAYS)) + thermal.STEP
        polls = self._mariadb.read_thermostat_polls(
            first - history, end + READING_MARGIN
        )
        if not polls:
            logger.info("Thermal model: no thermostat readings, nothing fitted.")
            return
        ledger = thermal.StepLedger(thermal.build_steps(polls, weather, first, end))
        fit = thermal.fit_window(ledger.window(0, start, end))
        result = None
        if fit.reason is None:
            result = thermal.backtest(ledger, days)
            fit = thermal.with_backtest(fit, result)
        self._mariadb.write_thermal_model(_record(fit, result, now, start, end))
        logger.info(
            f"Thermal model: fitted over {fit.steps_used} step(s), "
            f"{'usable' if fit.reason is None else fit.reason.value}."
        )

    def _fitted_today(self, now: datetime) -> bool:
        latest = self._mariadb.read_latest_thermal_model()
        return (
            latest is not None
            and latest.model.step_minutes == thermal.STEP_MINUTES
            and local_day.to_local_date(latest.model.fitted_at)
            == local_day.to_local_date(now)
        )


def _backtest_days(now: datetime) -> list[thermal.DayWindow]:
    """The 14 local days before the day of `now`, oldest first."""
    today = local_day.to_local_date(now)
    return [
        thermal.DayWindow(
            _naive_start_of(today - timedelta(days=ago)),
            _naive_start_of(today - timedelta(days=ago - 1)),
        )
        for ago in range(thermal.BACKTEST_DAYS, 0, -1)
    ]


def _naive_start_of(day: date) -> datetime:
    return local_day.start_of_local_day(day).replace(tzinfo=None)


def _record(
    fit: ThermalFit,
    result: thermal.Backtest | None,
    now: datetime,
    start: datetime,
    end: datetime,
) -> ThermalModelRecord:
    numbers = fit.numbers
    return ThermalModelRecord(
        fitted_at=now,
        window_start=local_day.to_local_date(start),
        # `end` is exclusive: the last fitted step starts a step before it.
        window_end=local_day.to_local_date(end - thermal.STEP),
        usable=fit.reason is None,
        unusable_reason=None if fit.reason is None else fit.reason.value,
        steps_used=fit.steps_used,
        heating_steps=fit.heating_steps,
        backtest_days=result.days if result else None,
        backtest_mean_error=result.mean_error if result else None,
        backtest_worst_error=result.worst_error if result else None,
        loss_per_degree=numbers.loss_per_degree if numbers else None,
        heating_gain=numbers.heating_gain if numbers else None,
        carried_gain=numbers.carried_gain if numbers else None,
        sun_gain=numbers.sun_gain if numbers else None,
        constant_gain=numbers.constant_gain if numbers else None,
        step_minutes=thermal.STEP_MINUTES,
    )

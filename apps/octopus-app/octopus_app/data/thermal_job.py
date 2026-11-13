"""Fits the thermal model once a local day and stores each fit (ADR-0035)."""

import logging.config
from dataclasses import asdict, fields
from datetime import UTC, date, datetime, timedelta
from logging import Logger, getLogger

from common import local_day
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import thermal
from octopus_app.data.model import ThermalModelRecord
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.thermal import ThermalFit, ThermalNumbers

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)


class ThermalModelJob:
    def __init__(self, mariadb: MariaDBClient) -> None:
        self._mariadb = mariadb

    def refresh(self, now: datetime | None = None) -> None:
        """Fits the model over the hourly steps of the 60 days before now and
        stores one row, usable or not. Does nothing when a fit was already made
        today, or when hive-app has stored no hourly weather or no thermostat
        readings to learn from."""
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
        readings = self._mariadb.read_room_temperatures(first, end + thermal.ONE_HOUR)
        if not readings:
            logger.info("Thermal model: no thermostat readings, nothing fitted.")
            return
        evidence = thermal.HourlyEvidence.from_readings(
            readings,
            self._mariadb.read_heating_hours(first - thermal.ONE_HOUR, end),
            weather,
        )
        fit = thermal.fit_thermal_model(thermal.hourly_steps(evidence, start, end))
        result = None
        if fit.reason is None:
            result = thermal.backtest(evidence, days)
            fit = thermal.with_backtest(fit, result)
        self._mariadb.write_thermal_model(_record(fit, result, now, start, end))
        logger.info(
            f"Thermal model: fitted over {fit.steps_used} step(s), "
            f"{'usable' if fit.reason is None else fit.reason.value}."
        )

    def _fitted_today(self, now: datetime) -> bool:
        latest = self._mariadb.read_latest_thermal_model()
        return latest is not None and local_day.to_local_date(
            latest.model.fitted_at
        ) == local_day.to_local_date(now)


def _backtest_days(now: datetime) -> list[thermal.DayHours]:
    """The 14 local days before the day of `now`, oldest first."""
    today = local_day.to_local_date(now)
    return [
        thermal.DayHours(
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
    numbers: dict[str, float | None] = (
        dict.fromkeys(field.name for field in fields(ThermalNumbers))
        if fit.numbers is None
        else asdict(fit.numbers)
    )
    return ThermalModelRecord(
        fitted_at=now,
        window_start=local_day.to_local_date(start),
        window_end=local_day.to_local_date(end),
        usable=fit.reason is None,
        unusable_reason=None if fit.reason is None else fit.reason.value,
        steps_used=fit.steps_used,
        heating_steps=fit.heating_steps,
        backtest_days=result.days if result else None,
        backtest_mean_error=result.mean_error if result else None,
        backtest_worst_error=result.worst_error if result else None,
        **numbers,  # type: ignore[arg-type]
    )

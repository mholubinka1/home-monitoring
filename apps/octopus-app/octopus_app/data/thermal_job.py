"""Fits the thermal model once a local day and stores each fit (ADR-0035,
ADR-0036)."""

import logging.config
from dataclasses import dataclass
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
LEARNED = "learned"
CARRIED = "carried"
NO_DELAY = "none"


@dataclass(frozen=True)
class _Delay:
    """The Warm-Up Delay a fit used: its index among the searched delays, how it
    was found, and the fit made with it."""

    index: int
    source: str
    fit: ThermalFit

    @property
    def minutes(self) -> int:
        return thermal.SEARCHED_DELAYS[self.index]


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
            first - history - thermal.QUIET_BEFORE_START, end + READING_MARGIN
        )
        if not polls:
            logger.info("Thermal model: no thermostat readings, nothing fitted.")
            return
        ledger = thermal.StepLedger(thermal.build_steps(polls, weather, first, end))
        heating_starts = thermal.heating_start_times(polls)
        starts_in_window = thermal.count_between(heating_starts, start, end)
        delay = self._delay(ledger, start, end, starts_in_window)
        fit = delay.fit
        result = None
        if fit.reason is None:
            result = thermal.backtest(ledger, days, delay.index, heating_starts)
            fit = thermal.with_backtest(fit, result)
        self._mariadb.write_thermal_model(
            _record(fit, delay, starts_in_window, result, now, start, end)
        )
        logger.info(
            f"Thermal model: fitted over {fit.steps_used} step(s) with a warm-up "
            f"delay of {delay.minutes} minute(s) ({delay.source}), "
            f"{'usable' if fit.reason is None else fit.reason.value}."
        )

    def _delay(
        self,
        ledger: thermal.StepLedger,
        start: datetime,
        end: datetime,
        starts_in_window: int,
    ) -> _Delay:
        """The delay whose fit misses the readings least, when the window holds
        enough Heating Starts to learn one; otherwise the newest stored delay, or
        none (0) when there has never been one."""
        if starts_in_window >= thermal.MIN_HEATING_STARTS:
            searched = thermal.search_delay(ledger, start, end)
            if searched is not None:
                return _Delay(searched[0], LEARNED, searched[1])
        carried = self._mariadb.read_latest_warm_up_delay()
        if carried in thermal.SEARCHED_DELAYS:
            index, source = thermal.SEARCHED_DELAYS.index(carried), CARRIED
        else:
            index, source = 0, NO_DELAY
        return _Delay(
            index, source, thermal.fit_window(ledger.window(index, start, end))
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


def _record(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    fit: ThermalFit,
    delay: _Delay,
    heating_starts: int,
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
        warm_up_delay_minutes=delay.minutes,
        warm_up_delay_source=delay.source,
        heating_starts=heating_starts,
    )

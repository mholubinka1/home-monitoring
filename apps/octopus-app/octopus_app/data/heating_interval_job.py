"""Recomputes the recent completed half-hours of heating and what each cost."""

import logging.config
from datetime import UTC, date, datetime, timedelta
from logging import Logger, getLogger

from common import local_day
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import heating_intervals
from octopus_app.data.model import Energy, HeatingIntervalRecord
from octopus_app.data.mysql.client import MariaDBClient

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

RECOMPUTE_LOCAL_DAYS = 4  # today and the three before it


class HeatingIntervalJob:
    def __init__(self, mariadb: MariaDBClient, region_code: str) -> None:
        self._mariadb = mariadb
        self._region_code = region_code
        self._computed_at = datetime.now(UTC).replace(tzinfo=None)

    def refresh(self, now: datetime | None = None) -> None:
        """Rewrites every half-hour that has ended in the last few local days. A
        half-hour without enough polls gets no row, and loses any row it had. When
        the newest stored one is older than those days (an empty table, or a
        stop), it first backfills a local day at a time from the half-hour after
        it."""
        now = (now or datetime.now(UTC)).astimezone(UTC)
        end = heating_intervals.interval_start_of(now.replace(tzinfo=None))
        today = local_day.to_local_date(now)
        window_first_day = today - timedelta(days=RECOMPUTE_LOCAL_DAYS - 1)
        resume_from = self._resume_from()
        if (
            resume_from is None
            or local_day.to_local_date(resume_from.replace(tzinfo=UTC))
            >= window_first_day
        ):
            self._recompute(now, self._day_start(window_first_day), end)
            return
        # A local day at a time, so each learns only from the rows before it.
        logger.info(f"Heating intervals: backfilling from {resume_from} UTC to {end}.")
        start = resume_from
        while start < end:
            day = local_day.to_local_date(start.replace(tzinfo=UTC))
            chunk_end = min(self._day_start(day + timedelta(days=1)), end)
            self._recompute(now, start, chunk_end)
            start = chunk_end

    def _resume_from(self) -> datetime | None:
        """Where a backfill starts (naive UTC): the end of the newest stored
        half-hour, so a job that stopped, or died part-way through a backfill or a
        day, carries on without rewriting what it stored; on an empty table the
        start of the first poll's local day. None with no polls."""
        latest_end = self._mariadb.read_latest_heating_interval_end()
        if latest_end is not None:
            return latest_end
        first_poll = self._mariadb.read_first_heating_poll()
        if first_poll is None:
            return None
        return self._day_start(local_day.to_local_date(first_poll.replace(tzinfo=UTC)))

    @staticmethod
    def _day_start(day: date) -> datetime:
        return local_day.start_of_local_day(day).astimezone(UTC).replace(tzinfo=None)

    def _recompute(self, now: datetime, start: datetime, end: datetime) -> None:
        # Polls from before the window, so a Heating Run that began earlier is seen whole.
        on_intervals = {
            interval: polls_on
            for interval, polls_on in heating_intervals.heating_on_minutes(
                self._mariadb.read_heating_polls(
                    start - heating_intervals.RUN_LOOKBACK, end
                )
            ).items()
            if interval >= start
        }
        gas = self._measured_gas(start, end)
        earlier = self._mariadb.read_heating_intervals(
            end - timedelta(days=heating_intervals.BASELINE_DAYS), start
        )
        baselines = self._baselines(earlier, on_intervals, gas)
        self._computed_at = now.replace(tzinfo=None)
        measured = [
            self._record(interval, polls_on, gas[interval], baselines)
            for interval, polls_on in sorted(on_intervals.items())
            if interval in gas
        ]
        power = self._boiler_power(earlier, measured)
        estimated = [
            self._record(interval, polls_on, None, baselines, power)
            for interval, polls_on in sorted(on_intervals.items())
            if interval not in gas
        ]
        rows = sorted(measured + estimated, key=lambda row: row.starts_at)
        self._mariadb.replace_heating_intervals(start, end, rows)
        logger.info(
            f"Heating intervals: wrote {len(rows)} half-hour(s) "
            f"({len(measured)} measured) from {start} to {end} UTC."
        )

    def _record(
        self,
        starts_at: datetime,
        polls_on: tuple[int, int, int],
        measured: float | None,
        baselines: dict[int, float],
        power: heating_intervals.BoilerPower = heating_intervals.DEFAULT_POWER,
    ) -> HeatingIntervalRecord:
        poll_count, on_minutes, early_on_minutes = polls_on
        unit_rate = self._mariadb.read_gas_unit_rate(self._region_code, starts_at)
        if measured is None:
            source = heating_intervals.ESTIMATED
            kwh, cost = heating_intervals.estimated_cost(
                on_minutes, early_on_minutes, unit_rate, power
            )
        else:
            source = heating_intervals.MEASURED
            kwh = (
                heating_intervals.measured_heating_kwh(
                    measured, heating_intervals.baseline_for(baselines, starts_at)
                )
                if on_minutes
                else 0.0
            )
            cost = None if unit_rate is None else kwh * unit_rate / 100
        return HeatingIntervalRecord(
            starts_at=starts_at,
            ends_at=starts_at + heating_intervals.INTERVAL,
            poll_count=poll_count,
            on_minutes=on_minutes,
            early_on_minutes=early_on_minutes,
            measured_kwh=measured,
            heating_kwh=kwh,
            source=source,
            unit_rate=unit_rate,
            cost_gbp=cost,
            computed_at=self._computed_at,
        )

    @staticmethod
    def _boiler_power(
        earlier: list[HeatingIntervalRecord], measured: list[HeatingIntervalRecord]
    ) -> heating_intervals.BoilerPower:
        """The boiler power learned from the measured half-hours stored in the 28
        days before the recompute window (`earlier`) plus those of the window
        itself; the single learned kW, both phases alike, when it cannot be."""
        rows = [
            row
            for row in earlier + measured
            if row.source == heating_intervals.MEASURED
        ]
        learned = heating_intervals.learned_boiler_power(
            (row.on_minutes, row.early_on_minutes, row.heating_kwh or 0.0)
            for row in rows
            if row.early_on_minutes is not None
        )
        if learned is not None:
            return learned
        kw = heating_intervals.learned_boiler_kw(
            (row.on_minutes, row.heating_kwh or 0.0) for row in rows
        )
        return heating_intervals.BoilerPower(kw, kw)

    def _measured_gas(self, start: datetime, end: datetime) -> dict[datetime, float]:
        """The metered gas of the half-hours in [start, end). Read from the start
        of `start`'s local day, so a run that resumes part-way through a day judges
        "the day's gas totals above zero" on the whole day, not on its tail."""
        day_start = self._day_start(local_day.to_local_date(start.replace(tzinfo=UTC)))
        readings = self._mariadb.read_half_hourly_consumption(
            Energy.gas, day_start.replace(tzinfo=UTC), end.replace(tzinfo=UTC)
        )
        return heating_intervals.measured_gas(
            (period_from, float(kwh)) for period_from, kwh in readings
        )

    @staticmethod
    def _baselines(
        earlier: list[HeatingIntervalRecord],
        on_intervals: dict[datetime, tuple[int, int, int]],
        gas: dict[datetime, float],
    ) -> dict[int, float]:
        """Hot-water baselines from the heating-off half-hours stored in the 28
        days before the recompute window (`earlier`) plus those of the window
        itself."""
        return heating_intervals.hot_water_baselines(
            [
                (row.starts_at, row.measured_kwh)
                for row in earlier
                if row.source == heating_intervals.MEASURED
                and row.on_minutes == 0
                and row.measured_kwh is not None
            ]
            + [
                (start, gas[start])
                for start, (_, on_minutes, _) in on_intervals.items()
                if on_minutes == 0 and start in gas
            ]
        )

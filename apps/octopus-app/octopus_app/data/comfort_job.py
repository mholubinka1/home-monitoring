"""Stores each local day's comfort temperature, counted hours and minutes below
comfort (ADR-0034, ADR-0040)."""

import logging.config
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from logging import Logger, getLogger

from common import local_day
from octopus_app.common.config import ComfortWindows
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import comfort, room_forecast
from octopus_app.data.model import ComfortDayRecord
from octopus_app.data.mysql.client import MariaDBClient

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

COUNTED_HOURS_WINDOW = timedelta(days=comfort.COUNTED_HOURS_WINDOW_DAYS)


@dataclass(frozen=True)
class _Evidence:
    """What a run reads once and every day it writes draws on: the heated hours
    (naive UTC) before those days, and the no_one_home Presence Periods during
    them."""

    heating_on_hours: list[datetime]
    no_one_home: list[tuple[datetime, datetime | None]]


class ComfortDayJob:
    def __init__(
        self, mariadb: MariaDBClient, comfort_windows: ComfortWindows | None = None
    ) -> None:
        self._mariadb = mariadb
        self._windows = comfort_windows or ComfortWindows()

    def refresh(self, now: datetime | None = None) -> None:
        """Writes the local days from the newest stored one to today, each starting
        at the value in force at the end of the day before and counting the heated
        hours of the year before it. So today is rewritten on every run, a finished day one last time on
        the first run after it ends, and older days never. An empty table is
        filled from the first thermostat poll's day; a day with no polls gets no
        row."""
        now = (now or datetime.now(UTC)).astimezone(UTC)
        today = local_day.to_local_date(now)
        first_day = self._first_day_to_write()
        if first_day is None:
            return
        evidence_start = _day_start(first_day) - COUNTED_HOURS_WINDOW
        evidence_end = _day_start(today)
        heating_on_hours = self._mariadb.read_heating_on_hours(
            evidence_start, evidence_end
        )
        evidence = _Evidence(
            heating_on_hours=heating_on_hours,
            no_one_home=self._mariadb.read_no_one_home_periods(
                _day_start(first_day), _day_start(today + timedelta(days=1))
            ),
        )
        previous = self._mariadb.read_previous_comfort_day(first_day)
        rows = []
        day = first_day
        while day <= today:
            row = self._comfort_day(day, evidence, previous, now)
            if row is not None:
                rows.append(row)
                previous = row
            day += timedelta(days=1)
        self._mariadb.write_comfort_days(rows)
        logger.info(
            f"Comfort days: wrote {len(rows)} day(s) from {first_day} to {today}."
        )

    def _first_day_to_write(self) -> date | None:
        latest = self._mariadb.read_latest_comfort_day()
        if latest is not None:
            day, computed_at = latest
            next_day = day + timedelta(days=1)
            # Written after it ended, so it is settled: never write it again.
            return next_day if computed_at >= _day_start(next_day) else day
        first_poll = self._mariadb.read_first_heating_poll()
        return None if first_poll is None else local_day.to_local_date(first_poll)

    def _comfort_day(
        self,
        day: date,
        evidence: _Evidence,
        previous: ComfortDayRecord | None,
        now: datetime,
    ) -> ComfortDayRecord | None:
        day_start = _day_start(day)
        day_end = _day_start(day + timedelta(days=1))
        # From one boost gap earlier, so a boost running across midnight is not
        # taken for a new one; only the day's own polls are used for the rest.
        thermostat_polls = self._mariadb.read_comfort_polls(
            day_start - comfort.BOOST_GAP, day_end
        )
        polls = [
            (poll.polled_at, poll.current_temp)
            for poll in thermostat_polls
            if poll.polled_at >= day_start
        ]
        if not polls:
            return None
        # Only the day's own periods, so a long backfill checks each poll against
        # a handful, not every period of the run.
        no_one_home_today = [
            (started, ended)
            for started, ended in evidence.no_one_home
            if started < day_end and (ended is None or ended > day_start)
        ]
        hours = comfort.counted_hours(
            hour
            for hour in evidence.heating_on_hours
            if day_start - COUNTED_HOURS_WINDOW <= hour < day_start
        )
        comfort_temp = comfort.value_in_force_at_end(previous)
        opens, closes = room_forecast.comfort_window(self._windows, day)
        raised_at = comfort.held_boost_start(
            thermostat_polls, comfort_temp, (_naive_utc(opens), _naive_utc(closes))
        )
        return ComfortDayRecord(
            date=day,
            comfort_temp=comfort_temp,
            boost_starts=len(
                [
                    start
                    for start, _ in comfort.boost_starts(
                        (poll.polled_at, poll.current_temp)
                        for poll in thermostat_polls
                        if poll.boost_active
                    )
                    if start >= day_start
                ]
            ),
            counted_hours=",".join(str(hour) for hour in hours),
            minutes_below=comfort.minutes_below(
                polls, comfort_temp, hours, no_one_home_today, raised_at
            ),
            computed_at=now.replace(tzinfo=None),
            raised_at=raised_at,
            day_kind=None if raised_at is None else "held_boost",
            quiet_count=0 if raised_at is not None else _quiet_count_at_end(previous),
        )


def _quiet_count_at_end(row: ComfortDayRecord | None) -> int:
    """The Quiet Days counted at the end of a stored day: none after a raise, with
    no row or before the stepped rule."""
    if row is None or row.raised_at is not None or row.quiet_count is None:
        return 0
    return row.quiet_count


def _naive_utc(local: datetime) -> datetime:
    return (
        local.replace(tzinfo=local_day.get_local_timezone())
        .astimezone(UTC)
        .replace(tzinfo=None)
    )


def _day_start(day: date) -> datetime:
    return local_day.start_of_local_day(day).astimezone(UTC).replace(tzinfo=None)

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
        """Writes the local days from the newest stored one to today, each
        starting at the value in force at the end of the day before and counting
        the heated hours of the year before it. So today is rewritten on every
        run, a finished day one last time on the first run after it ends, and
        older days never. An empty table is filled from the first thermostat
        poll's day; a day with no polls gets no row."""
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
            elif day == first_day:
                # No polls to rewrite it from: its stored row still leads on.
                previous = self._mariadb.read_previous_comfort_day(
                    day + timedelta(days=1)
                )
            day += timedelta(days=1)
        self._mariadb.write_comfort_days(rows)
        logger.info(
            f"Comfort days: wrote {len(rows)} day(s) from {first_day} to {today}."
        )

    def _first_day_to_write(self) -> date | None:
        latest = self._mariadb.read_latest_comfort_day()
        if latest is not None:
            day, computed_at = latest
            # A window that closes after midnight leaves the day before unsettled.
            before = day - timedelta(days=1)
            before_at = self._mariadb.read_comfort_day_computed_at(before)
            if before_at is not None and before_at < self._settled_from(before):
                return before
            # Written after it ended, so it is settled: never write it again.
            return (
                day + timedelta(days=1)
                if computed_at >= self._settled_from(day)
                else day
            )
        first_poll = self._mariadb.read_first_heating_poll()
        return None if first_poll is None else local_day.to_local_date(first_poll)

    def _settled_from(self, day: date) -> datetime:
        """When a day is over for good: its next midnight, or its window's close
        if that is later (naive UTC)."""
        _, closes = room_forecast.comfort_window(self._windows, day)
        return max(_day_start(day + timedelta(days=1)), local_day.naive_utc_of(closes))

    def _tail_of(self, previous: ComfortDayRecord | None) -> comfort.Tail | None:
        """The day before's window close (naive UTC), comfort temperature and
        raised_at: what a reading in its tail is compared with."""
        if previous is None or previous.comfort_temp is None:
            return None
        _, closes = room_forecast.comfort_window(self._windows, previous.date)
        return comfort.Tail(
            local_day.naive_utc_of(closes), previous.comfort_temp, previous.raised_at
        )

    def _comfort_day(
        self,
        day: date,
        evidence: _Evidence,
        previous: ComfortDayRecord | None,
        now: datetime,
    ) -> ComfortDayRecord | None:
        day_start = _day_start(day)
        day_end = _day_start(day + timedelta(days=1))
        opens, closes = room_forecast.comfort_window(self._windows, day)
        # From one boost gap earlier, so a boost running across midnight is not
        # taken for a new one, to the window's close if that is after midnight, so
        # the evening's evidence is whole; only the calendar day's own polls count
        # for minutes below and boost starts.
        evidence_end = max(day_end, local_day.naive_utc_of(closes))
        thermostat_polls = self._mariadb.read_comfort_polls(
            day_start - comfort.BOOST_GAP, evidence_end
        )
        days_polls = [
            poll for poll in thermostat_polls if day_start <= poll.polled_at < day_end
        ]
        if not days_polls:
            return None
        # Only the day's own periods, so a long backfill checks each poll against
        # a handful, not every period of the run.
        no_one_home_today = [
            (started, ended)
            for started, ended in evidence.no_one_home
            if started < evidence_end and (ended is None or ended > day_start)
        ]
        hours = comfort.counted_hours(
            hour
            for hour in evidence.heating_on_hours
            if day_start - COUNTED_HOURS_WINDOW <= hour < day_start
        )
        outcome = comfort.settle_day(
            previous,
            thermostat_polls,
            (local_day.naive_utc_of(opens), local_day.naive_utc_of(closes)),
            no_one_home_today,
        )
        return ComfortDayRecord(
            date=day,
            comfort_temp=outcome.comfort_temp,
            boost_starts=len(
                [
                    start
                    for start in comfort.boost_starts(
                        poll.polled_at for poll in thermostat_polls if poll.boost_active
                    )
                    if day_start <= start < day_end
                ]
            ),
            counted_hours=",".join(str(hour) for hour in hours),
            minutes_below=comfort.minutes_below(
                days_polls,
                outcome.comfort_temp,
                hours,
                no_one_home_today,
                outcome.raised_at,
                self._tail_of(previous),
            ),
            computed_at=now.replace(tzinfo=None),
            raised_at=outcome.raised_at,
            day_kind=outcome.day_kind,
            quiet_count=outcome.quiet_count,
        )


def _day_start(day: date) -> datetime:
    return local_day.start_of_local_day(day).astimezone(UTC).replace(tzinfo=None)

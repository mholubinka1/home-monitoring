"""Stores each local day's comfort temperature, counted hours and minutes below
comfort (ADR-0034)."""

import logging.config
from datetime import UTC, date, datetime, timedelta
from logging import Logger, getLogger

from common import local_day
from octopus_app.common.logging import APP_LOGGER_NAME, config
from octopus_app.data import comfort
from octopus_app.data.model import ComfortDayRecord
from octopus_app.data.mysql.client import MariaDBClient

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

LEARNING_WINDOW = timedelta(days=comfort.LEARNING_DAYS)


class ComfortDayJob:
    def __init__(self, mariadb: MariaDBClient) -> None:
        self._mariadb = mariadb

    def refresh(self, now: datetime | None = None) -> None:
        """Writes the local days from the newest stored one to today, each learned
        from the 90 days before it. So today is rewritten on every run, a finished
        day one last time on the first run after it ends, and older days never.
        An empty table is filled from the first thermostat poll's day; a day with
        no polls gets no row."""
        now = (now or datetime.now(UTC)).astimezone(UTC)
        today = local_day.to_local_date(now)
        first_day = self._first_day_to_write()
        if first_day is None:
            return
        evidence_start = _day_start(first_day) - LEARNING_WINDOW
        evidence_end = _day_start(today)
        boost_starts = comfort.boost_starts(
            self._mariadb.read_boost_polls(evidence_start, evidence_end)
        )
        heating_on_hours = self._mariadb.read_heating_on_hours(
            evidence_start, evidence_end
        )
        carried = self._mariadb.read_latest_comfort_temperature(first_day)
        rows = []
        day = first_day
        while day <= today:
            row = self._comfort_day(day, boost_starts, heating_on_hours, carried, now)
            if row is not None:
                rows.append(row)
                carried = row.comfort_temp
            day += timedelta(days=1)
        self._mariadb.write_comfort_days(rows)
        logger.info(
            f"Comfort days: wrote {len(rows)} day(s) from {first_day} to {today}."
        )

    def _first_day_to_write(self) -> date | None:
        latest = self._mariadb.read_latest_comfort_day()
        if latest is not None:
            return latest
        first_poll = self._mariadb.read_first_heating_poll()
        return None if first_poll is None else local_day.to_local_date(first_poll)

    def _comfort_day(
        self,
        day: date,
        boost_starts: list[comfort.BoostStart],
        heating_on_hours: list[datetime],
        carried: float | None,
        now: datetime,
    ) -> ComfortDayRecord | None:
        day_start = _day_start(day)
        polls = self._mariadb.read_room_temperatures(
            day_start, _day_start(day + timedelta(days=1))
        )
        if not polls:
            return None
        window_start = day_start - LEARNING_WINDOW
        start_temps = [
            room for at, room in boost_starts if window_start <= at < day_start
        ]
        hours = comfort.counted_hours(
            hour for hour in heating_on_hours if window_start <= hour < day_start
        )
        comfort_temp = comfort.comfort_temperature(start_temps, carried)
        return ComfortDayRecord(
            date=day,
            comfort_temp=comfort_temp,
            boost_starts=len(start_temps),
            counted_hours=",".join(str(hour) for hour in hours),
            minutes_below=(
                None
                if comfort_temp is None
                else comfort.minutes_below(polls, comfort_temp, hours)
            ),
            computed_at=now.replace(tzinfo=None),
        )


def _day_start(day: date) -> datetime:
    return local_day.start_of_local_day(day).astimezone(UTC).replace(tzinfo=None)

"""Stores each local day's comfort temperature (ADR-0034)."""

from datetime import UTC, date, datetime, timedelta

from common import local_day
from octopus_app.data import comfort
from octopus_app.data.model import ComfortDayRecord
from octopus_app.data.mysql.client import MariaDBClient


class ComfortDayJob:
    def __init__(self, mariadb: MariaDBClient) -> None:
        self._mariadb = mariadb

    def refresh(self, now: datetime | None = None) -> None:
        """Writes today's row, learned from the boosts of the 90 days before it."""
        now = (now or datetime.now(UTC)).astimezone(UTC)
        today = local_day.to_local_date(now)
        if self._mariadb.read_first_heating_poll() is None:
            return
        day_start = _day_start(today)
        starts = comfort.boost_starts(
            self._mariadb.read_boost_polls(
                day_start - timedelta(days=comfort.LEARNING_DAYS), day_start
            )
        )
        row = ComfortDayRecord(
            date=today,
            comfort_temp=comfort.comfort_temperature(
                [room for _, room in starts], None
            ),
            boost_starts=len(starts),
            counted_hours="",
            minutes_below=None,
            computed_at=now.replace(tzinfo=None),
        )
        self._mariadb.replace_comfort_days(today, today, [row])


def _day_start(day: date) -> datetime:
    return local_day.start_of_local_day(day).astimezone(UTC).replace(tzinfo=None)

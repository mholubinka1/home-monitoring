import logging.config
from datetime import datetime, timedelta
from logging import Logger, getLogger
from typing import Protocol

from common.local_day import get_local_timezone
from hive_app.common.config import QuietHours
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.mysql.client import MariaDBClient

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

LOST_AFTER = timedelta(minutes=30)
REMIND_EVERY = timedelta(minutes=60)
# The poll job whose failures explain a gap; main.py schedules and records it.
HEATING_REFRESH_JOB = "heating_refresh"


class DataNotifier(Protocol):
    def notify_data_lost(
        self, age: timedelta, reason: str | None, reminder: bool = False
    ) -> None: ...

    def notify_data_restored(self, outage: timedelta) -> None: ...


class HiveDataWatchdog:
    """Alerts when heating_status data stops arriving (see ADR-0031).

    Judged from the newest heating_status row, so a fresh process mid-outage
    alerts straight away. Whether a lost alert was delivered is in memory.
    """

    def __init__(
        self,
        mariadb: MariaDBClient,
        notifier: DataNotifier,
        quiet_hours: QuietHours | None = None,
    ) -> None:
        self._mariadb = mariadb
        self._notifier = notifier
        self._quiet_hours = quiet_hours
        # Newest data before the gap, set only once a lost alert was delivered.
        self._outage_began: datetime | None = None
        self._last_notified: datetime | None = None

    def check(self, now: datetime) -> None:
        if self._is_quiet(now):
            return
        latest = self._mariadb.latest_heating_polled_at()
        if latest is None:
            return
        age = now - latest
        if age > LOST_AFTER:
            self._close_an_outage_that_ended(latest)
            self._alert_lost(latest, age, now)
        else:
            self._announce_restored(latest)

    def _alert_lost(self, latest: datetime, age: timedelta, now: datetime) -> None:
        reminder = self._outage_began is not None
        if self._last_notified is not None and now - self._last_notified < REMIND_EVERY:
            return
        try:
            self._notifier.notify_data_lost(age, self._reason_after(latest), reminder)
        except Exception:
            logger.exception(
                "Failed to send Hive data-lost alert; will retry on the next check."
            )
            return
        if self._outage_began is None:
            self._outage_began = latest
        self._last_notified = now

    def _close_an_outage_that_ended(self, latest: datetime) -> None:
        """Data came back and then stopped again before a check saw it (for
        example across quiet hours): announce the first outage's end so the
        new gap is reported as a new outage, not a continuation."""
        if (
            self._outage_began is not None
            and self._mariadb.first_heating_polled_after(self._outage_began) is not None
        ):
            self._announce_restored(latest)

    def _reason_after(self, latest: datetime) -> str | None:
        """The latest poll failure since the newest row, if it can be read: the
        reason is a convenience and must never stop the alert."""
        try:
            return self._mariadb.latest_failed_job_error(HEATING_REFRESH_JOB, latest)
        except Exception:
            logger.exception("Could not read the latest poll failure for the alert.")
            return None

    def _announce_restored(self, latest: datetime) -> None:
        began = self._outage_began
        if began is None:
            return
        # Measured to the first row after the gap, not to this check, which
        # may be hours later if the data came back in quiet hours.
        returned = self._mariadb.first_heating_polled_after(began) or latest
        try:
            self._notifier.notify_data_restored(returned - began)
        except Exception:
            logger.exception(
                "Failed to send Hive data-restored notice; will retry on the next check."
            )
            return
        self._outage_began = None
        self._last_notified = None

    def _is_quiet(self, now: datetime) -> bool:
        """Nothing is sent in the quiet window; state is left as it was, so
        what is due goes out on the first check after it ends."""
        if self._quiet_hours is None:
            return False
        local = now.astimezone(get_local_timezone()).time()
        start, end = self._quiet_hours.start, self._quiet_hours.end
        if start <= end:
            return start <= local < end
        return local >= start or local < end

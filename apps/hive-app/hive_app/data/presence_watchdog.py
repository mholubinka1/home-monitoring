import logging.config
from datetime import datetime, timedelta
from logging import Logger, getLogger
from typing import Protocol

from hive_app.common.config import QuietHours
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.notify import describe_duration
from hive_app.data.presence import NO_ONE_HOME, UNKNOWN
from hive_app.data.quiet_hours import is_quiet
from hive_app.data.watchdog import LOST_AFTER, REMIND_EVERY

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

# Just after 3am, clear of the 01:00 and 02:00 clock-change hours.
NIGHT_CHECK_TIME = "03:05"


class PresenceNotifier(Protocol):
    def notify_presence_lost(self, age: timedelta, reminder: bool = False) -> None: ...

    def notify_presence_restored(self, outage: timedelta) -> None: ...


class PresenceWatchdog:
    """Alerts when Presence stays Unknown (the Pi-hole signal is lost).

    Judged from the open presence period, so a fresh process mid-outage alerts
    straight away. Whether a lost alert was delivered is in memory.
    """

    def __init__(
        self,
        mariadb: MariaDBClient,
        notifier: PresenceNotifier,
        quiet_hours: QuietHours | None = None,
    ) -> None:
        self._mariadb = mariadb
        self._notifier = notifier
        self._quiet_hours = quiet_hours
        # Start of the Unknown period, set only once a lost alert was delivered.
        self._outage_began: datetime | None = None
        self._last_notified: datetime | None = None

    def check(self, now: datetime) -> None:
        if is_quiet(self._quiet_hours, now):
            return
        period = self._mariadb.read_open_presence_period()
        if period is None:
            return
        state, started_at = period
        if state != UNKNOWN:
            # The known period began when the signal came back.
            self._announce_restored(started_at)
        elif now - started_at > LOST_AFTER:
            self._alert_lost(started_at, now)

    def _alert_lost(self, started_at: datetime, now: datetime) -> None:
        # shortcut: an earlier outage's end was never seen (quiet hours), so it
        # is measured to this one's start; the exact end is not stored.
        if (
            self._outage_began is not None
            and started_at > self._outage_began
            and not self._announce_restored(started_at)
        ):
            return
        if self._last_notified is not None and now - self._last_notified < REMIND_EVERY:
            return
        reminder = self._outage_began is not None
        try:
            self._notifier.notify_presence_lost(now - started_at, reminder)
        except Exception:
            logger.exception(
                "Failed to send presence-lost alert; will retry on the next check."
            )
            return
        self._outage_began = started_at
        self._last_notified = now

    def _announce_restored(self, returned: datetime) -> bool:
        """Sends the restored notice if an outage is open; False only if the
        send failed."""
        began = self._outage_began
        if began is None:
            return True
        try:
            self._notifier.notify_presence_restored(returned - began)
        except Exception:
            logger.exception(
                "Failed to send presence-restored notice; will retry on the next check."
            )
            return False
        self._outage_began = None
        self._last_notified = None
        return True


def warn_if_house_empty(mariadb: MariaDBClient, now: datetime) -> None:
    """The 03:05 check: an empty house at night is worth a WARNING."""
    period = mariadb.read_open_presence_period()
    if period is not None and period[0] == NO_ONE_HOME:
        logger.warning(
            f"The house has been empty for {describe_duration(now - period[1])} "
            f"at {NIGHT_CHECK_TIME}."
        )

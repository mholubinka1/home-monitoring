import logging.config
from datetime import datetime, timedelta
from logging import Logger, getLogger
from typing import Protocol

from hive_app.common.config import QuietHours
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.quiet_hours import is_quiet
from hive_app.data.watchdog import LOST_AFTER

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

# Bounds the poll reads on the Pi's small MariaDB; a longer spell starts here.
SPELL_WINDOW = timedelta(days=7)


class HeatingNotifier(Protocol):
    def notify_heating_unreachable(self, age: timedelta, reason: str) -> None: ...

    def notify_heating_reachable(self, outage: timedelta) -> None: ...


class HeatingWatchdog:
    """Alerts when stored polls show Hive offline or OFF for over 30 minutes."""

    def __init__(
        self,
        mariadb: MariaDBClient,
        notifier: HeatingNotifier,
        quiet_hours: QuietHours | None = None,
    ) -> None:
        self._mariadb = mariadb
        self._notifier = notifier
        self._quiet_hours = quiet_hours
        # Start of the spell whose alert was delivered.
        self._alerted_spell: datetime | None = None

    def check(self, now: datetime) -> None:
        if is_quiet(self._quiet_hours, now):
            return
        spell = self._mariadb.read_unreachable_spell(now - SPELL_WINDOW)
        if not self._announce_reachable(now):
            return
        if spell is None:
            return
        started_at, reason = spell
        if now - started_at <= LOST_AFTER or self._alerted_spell is not None:
            return
        try:
            self._notifier.notify_heating_unreachable(now - started_at, reason)
        except Exception:
            logger.exception(
                "Failed to send heating-unreachable alert; will retry on the next check."
            )
            return
        self._alerted_spell = started_at

    def _announce_reachable(self, now: datetime) -> bool:
        """Sends the reachable notice if an alerted spell has ended; False only if
        the send failed. The spell has ended when a reachable poll followed its
        start, even if a new spell began since (its start slides with the window
        when it is long, so it is not compared). Only the last SPELL_WINDOW of
        polls is searched, so a months-long spell is never read in full."""
        began = self._alerted_spell
        if began is None:
            return True
        returned = self._mariadb.first_reachable_heating_polled_after(
            max(began, now - SPELL_WINDOW)
        )
        if returned is None:
            return True
        try:
            self._notifier.notify_heating_reachable(returned - began)
        except Exception:
            logger.exception(
                "Failed to send heating-reachable notice; will retry on the next check."
            )
            return False
        self._alerted_spell = None
        return True

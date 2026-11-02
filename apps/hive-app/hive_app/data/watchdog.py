import logging.config
from datetime import datetime, timedelta
from logging import Logger, getLogger
from typing import Protocol

from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.mysql.client import MariaDBClient

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)

LOST_AFTER = timedelta(minutes=30)
HEATING_REFRESH_JOB = "heating_refresh"


class DataNotifier(Protocol):
    def notify_data_lost(self, age: timedelta, reason: str | None) -> None: ...

    def notify_data_restored(self, outage: timedelta) -> None: ...


class HiveDataWatchdog:
    """Alerts when heating_status data stops arriving (see ADR-0031).

    Judged from the newest heating_status row, so a fresh process mid-outage
    alerts straight away. Whether a lost alert was delivered is in memory.
    """

    def __init__(self, mariadb: MariaDBClient, notifier: DataNotifier) -> None:
        self._mariadb = mariadb
        self._notifier = notifier
        # Newest data before the gap, set only once a lost alert was delivered.
        self._outage_began: datetime | None = None

    def check(self, now: datetime) -> None:
        latest = self._mariadb.latest_heating_polled_at()
        if latest is None:
            return
        age = now - latest
        if age > LOST_AFTER:
            self._alert_lost(latest, age)
        else:
            self._announce_restored(now)

    def _alert_lost(self, latest: datetime, age: timedelta) -> None:
        if self._outage_began is not None:
            return
        try:
            self._notifier.notify_data_lost(
                age, self._mariadb.latest_failed_job_error(HEATING_REFRESH_JOB)
            )
        except Exception:
            logger.exception(
                "Failed to send Hive data-lost alert; will retry on the next check."
            )
            return
        self._outage_began = latest

    def _announce_restored(self, now: datetime) -> None:
        began, self._outage_began = self._outage_began, None
        if began is None:
            return
        try:
            self._notifier.notify_data_restored(now - began)
        except Exception:
            logger.exception("Failed to send Hive data-restored notice; not retrying.")

from datetime import datetime, timedelta
from logging import Logger, getLogger

from hive_app.common.config import PresenceSettings
from hive_app.common.logging import APP_LOGGER_NAME
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.phone_rule import PhoneRule
from hive_app.data.pihole import PiholeDevice, PiholeError, PiholeSource

logger: Logger = getLogger(APP_LOGGER_NAME)

SOMEONE_HOME = "someone_home"
NO_ONE_HOME = "no_one_home"
UNKNOWN = "unknown"


class PresenceRetriever:
    """Decides who is home from Pi-hole's phones and records each change of
    state as a presence period. Keeps no state: the open period is read from
    the database, so a restart continues it."""

    def __init__(
        self,
        source: PiholeSource,
        mariadb: MariaDBClient,
        settings: PresenceSettings,
    ) -> None:
        self._source = source
        self._mariadb = mariadb
        self._phone_rule = PhoneRule(
            settings.phone_hostnames, settings.ignored_hostnames
        )
        self._away_delay = timedelta(minutes=settings.away_delay_minutes)

    def refresh(self, now: datetime) -> None:
        state, phones_seen = self._decide(now)
        if state == self._mariadb.read_open_presence_state():
            return
        self._mariadb.change_presence_state(state, phones_seen, now)
        logger.info(f"Presence is now {state} ({phones_seen} phone(s) active).")

    def _decide(self, now: datetime) -> tuple[str, int]:
        try:
            devices = self._source.read_devices()
        except PiholeError as error:
            logger.warning(f"Presence unknown: {error}")
            return UNKNOWN, 0
        active_phones = [
            device for device in devices if self._is_active_phone(device, now)
        ]
        for device in active_phones:
            logger.debug(f"Active phone: {device.hostnames}")
        return (SOMEONE_HOME if active_phones else NO_ONE_HOME), len(active_phones)

    def _is_active_phone(self, device: PiholeDevice, now: datetime) -> bool:
        return (
            device.last_query is not None
            and device.last_query >= now - self._away_delay
            and self._phone_rule.is_phone(device.hwaddr, device.hostnames)
        )

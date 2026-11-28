import logging
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
        self._last_seen_window = timedelta(minutes=settings.last_seen_minutes)
        self._dns_window = timedelta(minutes=settings.dns_minutes)

    def refresh(self, now: datetime) -> None:
        open_state = self._mariadb.read_open_presence_state()
        state, phones_seen = self._decide(now, open_state)
        if state == open_state:
            return
        self._mariadb.change_presence_state(state, phones_seen, now)
        detail = "" if state == UNKNOWN else f" ({phones_seen} phone(s) active)"
        logger.info(f"Presence is now {state}{detail}.")

    def _decide(self, now: datetime, open_state: str | None) -> tuple[str, int]:
        try:
            devices = self._source.read_devices()
        except PiholeError as error:
            # Once per outage: the next runs, every minute, would only repeat it.
            level = logging.DEBUG if open_state == UNKNOWN else logging.WARNING
            logger.log(level, f"Presence unknown: {error}")
            return UNKNOWN, 0
        active_phones = [
            device for device in devices if self._is_active_phone(device, now)
        ]
        for device in active_phones:
            logger.debug(f"Active phone: {device.hostnames}")
        return (SOMEONE_HOME if active_phones else NO_ONE_HOME), len(active_phones)

    def _is_active_phone(self, device: PiholeDevice, now: datetime) -> bool:
        """A Phone last seen on the network within the last-seen window, or that
        made a DNS lookup within the DNS window (ADR-0042)."""
        seen = device.last_seen is not None and (
            device.last_seen >= now - self._last_seen_window
        )
        queried = device.last_query is not None and (
            device.last_query >= now - self._dns_window
        )
        return (seen or queried) and self._phone_rule.is_phone(
            device.hwaddr, device.hostnames
        )

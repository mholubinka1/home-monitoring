import logging.config
from logging import Logger, getLogger
from typing import Protocol

from hive_app.common.exceptions import HiveReauthRequired
from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.model import HeatingStatus, HiveAuthState
from hive_app.data.notify import ReauthAlert

logging.config.dictConfig(config)
logger: Logger = getLogger(APP_LOGGER_NAME)


class HiveSource(Protocol):
    def fetch_heating_status(self) -> HeatingStatus: ...

    def persist_heating_status(self, status: HeatingStatus) -> None: ...

    def read_auth_state(self) -> HiveAuthState | None: ...

    def login(self) -> HiveAuthState: ...

    def resume(self, state: HiveAuthState) -> HiveAuthState: ...

    def persist_auth_state(self, state: HiveAuthState) -> None: ...


class HeatingRetriever:
    _client: HiveSource
    _alert: ReauthAlert | None

    def __init__(self, client: HiveSource, alert: ReauthAlert | None = None) -> None:
        self._client = client
        self._alert = alert

    def refresh(self) -> None:
        try:
            status = self._client.fetch_heating_status()
        except HiveReauthRequired:
            if self._alert is not None:
                self._alert.notify_once()
            raise
        if self._alert is not None:
            self._alert.clear()
        self._client.persist_heating_status(status)

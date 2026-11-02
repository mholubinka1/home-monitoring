import logging.config
from logging import Logger, getLogger
from typing import Protocol

from hive_app.common.logging import APP_LOGGER_NAME, config
from hive_app.data.model import HeatingStatus, HiveAuthState

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

    def __init__(self, client: HiveSource) -> None:
        self._client = client

    def refresh(self) -> None:
        status = self._client.fetch_heating_status()
        self._client.persist_heating_status(status)

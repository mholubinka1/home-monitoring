from dataclasses import dataclass
from datetime import UTC, datetime
from logging import Logger, getLogger
from typing import Protocol

import requests
from pydantic import (
    BaseModel,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
)

from hive_app.common.config import PiholeSettings
from hive_app.common.logging import APP_LOGGER_NAME

logger: Logger = getLogger(APP_LOGGER_NAME)

REQUEST_TIMEOUT_SECONDS = 10
MAX_DEVICES = 500
MAX_ADDRESSES = 20
SESSION_HEADER = "X-FTL-SID"
NEVER_QUERIED = 0


class PiholeError(Exception):
    """Pi-hole could not be read; the message never carries the password."""


@dataclass(frozen=True)
class PiholeDevice:
    hwaddr: str
    hostnames: list[str]
    last_query: datetime | None


class PiholeSource(Protocol):
    def read_devices(self) -> list[PiholeDevice]: ...


class _Address(BaseModel):
    name: StrictStr | None = None


class _Device(BaseModel):
    hwaddr: StrictStr | None
    lastQuery: StrictInt | StrictFloat
    ips: list[_Address]

    def to_device(self) -> PiholeDevice:
        return PiholeDevice(
            hwaddr=self.hwaddr or "",
            hostnames=[address.name for address in self.ips if address.name],
            last_query=(
                datetime.fromtimestamp(self.lastQuery, UTC)
                if self.lastQuery != NEVER_QUERIED
                else None
            ),
        )


class _Session(BaseModel):
    sid: StrictStr = Field(min_length=1)


class _LoginResponse(BaseModel):
    session: _Session


class _DevicesResponse(BaseModel):
    devices: list[_Device]


class PiholeApiSource:
    """Reads devices from the Pi-hole v6 REST API, reusing one login session
    across polls."""

    def __init__(self, settings: PiholeSettings) -> None:
        self._base_url = settings.url.rstrip("/")
        self._password = settings.password
        self._session_id: str | None = None

    def read_devices(self) -> list[PiholeDevice]:
        try:
            response = self._get_devices()
            if response.status_code == requests.codes.unauthorized:
                self._session_id = None
                response = self._get_devices()
            response.raise_for_status()
            body = _DevicesResponse.model_validate(response.json())
            devices = [device.to_device() for device in body.devices]
            if not devices:
                raise ValueError("Pi-hole knows no devices")
            return devices
        except (
            requests.RequestException,
            ValidationError,
            ValueError,
            OverflowError,
            OSError,
        ) as error:
            raise PiholeError(
                f"Pi-hole could not be read ({type(error).__name__})"
            ) from None

    def logout(self) -> None:
        if self._session_id is None:
            return
        try:
            requests.delete(
                f"{self._base_url}/api/auth",
                headers={SESSION_HEADER: self._session_id},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            logger.warning(f"Pi-hole logout failed ({type(error).__name__}).")
        self._session_id = None

    def _get_devices(self) -> requests.Response:
        if self._session_id is None:
            self._session_id = self._login()
        return requests.get(
            f"{self._base_url}/api/network/devices",
            params={"max_devices": MAX_DEVICES, "max_addresses": MAX_ADDRESSES},
            headers={SESSION_HEADER: self._session_id},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    def _login(self) -> str:
        response = requests.post(
            f"{self._base_url}/api/auth",
            json={"password": self._password},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return _LoginResponse.model_validate(response.json()).session.sid

from dataclasses import dataclass
from datetime import UTC, datetime
from logging import Logger, getLogger
from typing import Any, Protocol

import requests

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


def _parse_device(raw: dict[str, Any]) -> PiholeDevice:
    last_query = raw["lastQuery"]
    return PiholeDevice(
        hwaddr=raw["hwaddr"] or "",
        hostnames=[ip["name"] for ip in raw["ips"] if ip.get("name")],
        last_query=(
            datetime.fromtimestamp(last_query, UTC)
            if last_query != NEVER_QUERIED
            else None
        ),
    )


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
            return [_parse_device(raw) for raw in response.json()["devices"]]
        except (
            requests.RequestException,
            KeyError,
            TypeError,
            AttributeError,
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
        session_id = response.json()["session"]["sid"]
        if not session_id:
            raise ValueError("Pi-hole returned no session id")
        return str(session_id)

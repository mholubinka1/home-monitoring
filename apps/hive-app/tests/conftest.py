from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.mysql.model import SQLBase


@pytest.fixture
def mariadb_client(monkeypatch: pytest.MonkeyPatch) -> MariaDBClient:
    """A hive_app MariaDBClient backed by an in-memory SQLite database.

    schema="octopus" is translated to "main" (SQLite's default database) --
    see ADR-0025 for why this fixture's own map and database="main" below
    must agree with SessionBuilder's own schema_translate_map.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"octopus": "main"})
    SQLBase.metadata.create_all(engine)

    monkeypatch.setattr(
        "common.mariadb.client.create_engine",
        lambda *args, **kwargs: engine,
    )

    settings = MariaDBSettings(
        host="localhost",
        port=3306,
        database="main",
        username="test",
        password="test",
    )
    return MariaDBClient(settings)


@pytest.fixture
def unsynced_mariadb_client_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], MariaDBClient]:
    """Builds a hive_app MariaDBClient against an EMPTY in-memory SQLite
    database: unlike mariadb_client, no tables are pre-created, so only the
    client's own startup Schema Sync can have created them.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"octopus": "main"})

    monkeypatch.setattr(
        "common.mariadb.client.create_engine",
        lambda *args, **kwargs: engine,
    )

    settings = MariaDBSettings(
        host="localhost",
        port=3306,
        database="main",
        username="test",
        password="test",
    )
    return lambda: MariaDBClient(settings)


class FakeApyHive:  # pylint: disable=too-many-instance-attributes
    """Stands in for apyhiveapi's real `Hive` object at the system boundary
    (see .agent-docs/agent.md's "mock only at system boundaries" rule).
    The SMS flow mirrors apyhiveapi==1.0.9's HiveSession.login/sms2fa and
    HiveAuthAsync.device_registration: login() returns the raw SMS_MFA
    challenge (carrying a "Session"), sms2fa() exchanges the code for an
    AuthenticationResult with NewDeviceMetadata, and device_registration()
    confirms the device so later logins can skip SMS."""

    def __init__(
        self,
        sms_result: dict[str, Any],
        login_result: dict[str, Any] | None = None,
        sms_error: Exception | None = None,
        registration_sets_password: bool = True,
        **_: Any,
    ) -> None:
        self._sms_result = sms_result
        self._registration_sets_password = registration_sets_password
        self._login_result = login_result or {
            "ChallengeName": "SMS_MFA",
            "Session": "sms-session",
        }
        self._sms_error = sms_error
        self.tokens = SimpleNamespace(tokenData={})
        self.auth = SimpleNamespace(
            device_group_key=None,
            device_key=None,
            device_password=None,
            SMS_MFA_CHALLENGE="SMS_MFA",
            device_registration=self._device_registration,
        )
        self.api = SimpleNamespace(getAll=self._get_all)
        self.deviceList: dict[str, Any] = {}
        self.call_order: list[str] = []
        self.submitted_codes: list[str] = []
        self.submitted_sessions: list[str] = []
        self.start_session_configs: list[dict[str, Any] | None] = []

    async def _get_all(self) -> dict[str, Any]:
        return {}

    async def login(self) -> dict[str, Any]:
        self.call_order.append("login")
        # Like the real login(): a direct AuthenticationResult records its
        # tokens and, only when Cognito offered NewDeviceMetadata, the device
        # group key and device key -- never the device password, which only
        # device_registration() generates.
        auth_result = self._login_result.get("AuthenticationResult")
        if auth_result is not None:
            for result_key, token_name in (
                ("IdToken", "token"),
                ("AccessToken", "accessToken"),
                ("RefreshToken", "refreshToken"),
            ):
                if result_key in auth_result:
                    self.tokens.tokenData[token_name] = auth_result[result_key]
            metadata = auth_result.get("NewDeviceMetadata")
            if metadata is not None:
                self.auth.device_group_key = metadata["DeviceGroupKey"]
                self.auth.device_key = metadata["DeviceKey"]
        return self._login_result

    async def sms2fa(self, code: str, session: str) -> dict[str, Any]:
        self.call_order.append("sms2fa")
        if self._sms_error is not None:
            raise self._sms_error
        self.submitted_codes.append(code)
        self.submitted_sessions.append(session)
        auth_result = self._sms_result["AuthenticationResult"]
        self.tokens.tokenData["token"] = auth_result["IdToken"]
        self.tokens.tokenData["refreshToken"] = auth_result["RefreshToken"]
        self.tokens.tokenData["accessToken"] = auth_result["AccessToken"]
        # Like the real sms_2fa, only records device keys when Cognito
        # actually offered NewDeviceMetadata.
        metadata = auth_result.get("NewDeviceMetadata")
        if metadata is not None:
            self.auth.device_group_key = metadata["DeviceGroupKey"]
            self.auth.device_key = metadata["DeviceKey"]
        return self._sms_result

    async def _device_registration(self) -> None:
        self.call_order.append("device_registration")
        if self._registration_sets_password:
            self.auth.device_password = "generated-device-password"

    async def startSession(self, config: dict[str, Any] | None = None) -> None:
        self.call_order.append("startSession")
        self.start_session_configs.append(config)
        self.deviceList = {"climate": [{"id": "thermostat-1"}]}


SMS_SUCCESS = {
    "AuthenticationResult": {
        "IdToken": "id-tok",
        "AccessToken": "access-tok",
        "RefreshToken": "refresh-tok",
        "NewDeviceMetadata": {
            "DeviceGroupKey": "group-key",
            "DeviceKey": "device-key",
        },
    }
}


@pytest.fixture
def install_fake_hive(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeApyHive]:
    """Builds a FakeApyHive and installs it in place of apyhiveapi's `Hive`
    as constructed by hive_client."""

    def install(**kwargs: Any) -> FakeApyHive:
        sms_result = kwargs.pop("sms_result", SMS_SUCCESS)
        fake_hive = FakeApyHive(sms_result, **kwargs)
        monkeypatch.setattr("hive_app.data.hive_client.Hive", lambda **_: fake_hive)
        return fake_hive

    return install

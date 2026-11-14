import copy
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from apyhiveapi.helper.hive_helper import HiveHelper
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from common.local_day import DEFAULT_TIMEZONE, set_local_timezone
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.mysql.model import SQLBase


@pytest.fixture(autouse=True)
def _reset_local_timezone() -> Iterator[None]:
    """The active zone is process-wide state: put it back after every test."""
    yield
    set_local_timezone(DEFAULT_TIMEZONE)


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


THERMOSTAT_HIVE_ID = "thermostat-hive-id"


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
        heating_working: Any = None,
        **options: Any,
    ) -> None:
        self._sms_result = sms_result
        # As apyhiveapi exposes it: the Hive object is its own session, and
        # its product data is keyed by the device's hiveID. `product_state` is
        # that product's `state` (empty when not given), reloaded as a fresh
        # copy each time a session starts, as each real poll's new Hive loads
        # fresh JSON.
        self._product_state = options.get("product_state") or {}
        self.session = SimpleNamespace(data=SimpleNamespace(products={}))
        self._load_products()
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
        self.heating = self._heating_namespace(
            heating_working, options.get("heating_mode", "SCHEDULE")
        )
        self.heating.getScheduleNowNextLater = self._schedule_now_next_later
        self.deviceList: dict[str, Any] = {}
        self.call_order: list[str] = []
        self.submitted_codes: list[str] = []
        self.submitted_sessions: list[str] = []
        self.start_session_configs: list[dict[str, Any] | None] = []

    async def _get_all(self) -> dict[str, Any]:
        return {}

    async def _schedule_now_next_later(
        self, device: dict[str, Any]
    ) -> dict[str, Any] | None:
        """As apyhiveapi's heating.getScheduleNowNextLater: in SCHEDULE mode
        the library's own helper parses the product schedule, writing
        datetimes into its slots in place; a KeyError gives None."""
        if await self.heating.getMode(device) != "SCHEDULE":
            return None
        try:
            state = self.session.data.products[device["hiveID"]]["state"]
            return HiveHelper().getScheduleNNL(state["schedule"])
        except KeyError:
            return None

    def _load_products(self) -> None:
        self.session.data.products = {
            THERMOSTAT_HIVE_ID: {"state": copy.deepcopy(self._product_state)}
        }

    @staticmethod
    def _heating_namespace(working: Any, mode: str) -> SimpleNamespace:
        """The heating getters a poll calls; `working` is whatever
        getCurrentOperation reports (None when the API omits the key) and
        `mode` is what getMode reports."""

        def constant(value: Any) -> Any:
            async def getter(_device: dict[str, Any]) -> Any:
                return value

            return getter

        return SimpleNamespace(
            getCurrentTemperature=constant(19.5),
            getTargetTemperature=constant(21.0),
            getMode=constant(mode),
            getState=constant("ON"),
            getBoostStatus=constant("OFF"),
            getCurrentOperation=constant(working),
        )

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
        self._load_products()
        self.deviceList = {
            "climate": [{"id": "thermostat-1", "hiveID": THERMOSTAT_HIVE_ID}]
        }


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

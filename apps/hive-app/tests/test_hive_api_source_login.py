from types import SimpleNamespace
from typing import Any

import pytest

from hive_app.common.config import HiveSettings
from hive_app.common.exceptions import HiveReauthRequired
from hive_app.data.hive_client import HiveApiSource
from hive_app.data.mysql.client import MariaDBClient


class _FakeApyHive:
    """Stands in for apyhiveapi's real `Hive` object -- the actual system
    boundary this repo's code talks to (see .agent-docs/agent.md's "mock
    only at system boundaries" rule). Response shapes and call sequence
    below are copied from apyhiveapi==1.0.9's real installed source
    (HiveSession.login/startSession/getDevices/createDevices/updateTokens),
    not invented."""

    def __init__(self, login_result: dict[str, Any], **_: Any) -> None:
        self._login_result = login_result
        self.tokens = SimpleNamespace(tokenData={})
        self.auth = SimpleNamespace(
            device_group_key=None,
            device_key=None,
            device_password=None,
            # Real value confirmed against apyhiveapi==1.0.9's installed
            # source (HiveAuthAsync.SMS_MFA_CHALLENGE / HiveAuth.
            # SMS_MFA_CHALLENGE), not invented.
            SMS_MFA_CHALLENGE="SMS_MFA",
        )
        self.deviceList: dict[str, Any] = {}
        self.login_call_count = 0
        self.get_devices_call_count = 0
        self.create_devices_call_count = 0
        self.start_session_configs: list[dict[str, Any] | None] = []

    async def login(self) -> dict[str, Any]:
        self.login_call_count += 1
        # Mirrors the real library's own login(): on full success it calls
        # self.updateTokens(result) internally, populating tokenData before
        # returning -- callers read the refresh token back off
        # hive.tokens.tokenData, not out of the raw login() result (see
        # HiveSession.login/updateTokens).
        auth_result = self._login_result.get("AuthenticationResult")
        if auth_result is not None:
            self.tokens.tokenData["token"] = auth_result.get("IdToken", "")
            if "RefreshToken" in auth_result:
                self.tokens.tokenData["refreshToken"] = auth_result["RefreshToken"]
            self.tokens.tokenData["accessToken"] = auth_result.get("AccessToken", "")
        return self._login_result

    async def startSession(self, config: dict[str, Any] | None = None) -> None:
        # Mirrors HiveSession.startSession's real structure end to end, not
        # just its token-handling: config=None defaults to {}; a non-empty
        # config's "tokens" key is applied via the same (surprising,
        # unconditional-overwrite) logic real updateTokens() uses -- the
        # production bug this test suite exists to catch was exactly this:
        # routing a fresh login's real tokens back through this path with
        # blank id/access-token placeholders wipes them, since this branch
        # does not merge or skip blanks. Then, matching the real method's
        # own tail exactly (session.py's startSession, after its config
        # block): getDevices() runs unconditionally, followed by
        # createDevices() -- the method that actually builds deviceList.
        # getDevices() alone does NOT populate deviceList; an earlier
        # version of this fix called only getDevices() directly and missed
        # this, a second bug this fix's own code review caught.
        if config is None:
            config = {}
        self.start_session_configs.append(config)
        if config != {} and "tokens" in config:
            self.tokens.tokenData["token"] = config["tokens"]["token"]
            self.tokens.tokenData["refreshToken"] = config["tokens"]["refreshToken"]
            self.tokens.tokenData["accessToken"] = config["tokens"]["accessToken"]
        await self.getDevices("No_ID")
        await self.createDevices()

    async def getDevices(self, _n_id: str) -> None:
        self.get_devices_call_count += 1

    async def createDevices(self) -> dict[str, Any]:
        # Real createDevices() builds deviceList from whatever getDevices()
        # populated on self.data -- simplified here to a single climate
        # device, enough to prove HiveApiSource's own _climate_device
        # (which reads hive.deviceList.get("climate", [])) would find one.
        self.create_devices_call_count += 1
        self.deviceList = {"climate": [{"id": "thermostat-1"}]}
        return self.deviceList


def _hive_settings() -> HiveSettings:
    return HiveSettings(username="user@example.com", password="hunter2")


def test_a_fresh_login_with_no_challenge_completes_and_populates_devices(
    mariadb_client: MariaDBClient, monkeypatch: Any
) -> None:
    login_result = {
        "AuthenticationResult": {
            "IdToken": "id-tok",
            "AccessToken": "access-tok",
            "RefreshToken": "refresh-tok",
            "ExpiresIn": 3600,
        }
    }
    fake_hive = _FakeApyHive(login_result)
    monkeypatch.setattr("hive_app.data.hive_client.Hive", lambda **kwargs: fake_hive)

    state = HiveApiSource(_hive_settings(), mariadb_client).login()

    assert fake_hive.login_call_count == 1
    # The follow-up startSession() call is passed an explicitly empty
    # config -- proving the real tokens login() just obtained are never
    # handed back through startSession()'s token-processing block (which
    # would clobber them, see _FakeApyHive.startSession's own comment).
    assert fake_hive.start_session_configs == [{}]
    assert fake_hive.get_devices_call_count == 1
    assert fake_hive.create_devices_call_count == 1
    # Proves the second bug this fix's code review caught is closed too:
    # deviceList (built only by createDevices(), not getDevices() alone)
    # is actually populated, not left empty after a fresh login.
    assert fake_hive.deviceList == {"climate": [{"id": "thermostat-1"}]}
    # The regression the first review round caught: login()'s real tokens
    # must survive completely untouched afterward, not just refreshToken.
    assert fake_hive.tokens.tokenData == {
        "token": "id-tok",
        "refreshToken": "refresh-tok",
        "accessToken": "access-tok",
    }
    assert state.refresh_token == "refresh-tok"


def test_a_fresh_login_that_hits_the_sms_challenge_requires_reauth(
    mariadb_client: MariaDBClient, monkeypatch: Any
) -> None:
    login_result = {"ChallengeName": "SMS_MFA"}
    fake_hive = _FakeApyHive(login_result)
    monkeypatch.setattr("hive_app.data.hive_client.Hive", lambda **kwargs: fake_hive)

    with pytest.raises(HiveReauthRequired) as exc_info:
        HiveApiSource(_hive_settings(), mariadb_client).login()

    assert (
        str(exc_info.value)
        == "Hive login requires a live SMS 2FA code; a headless service "
        "cannot supply one."
    )
    assert fake_hive.login_call_count == 1
    assert not fake_hive.start_session_configs
    assert fake_hive.get_devices_call_count == 0
    assert fake_hive.create_devices_call_count == 0

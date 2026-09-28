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
    only at system boundaries" rule). Response shapes below are copied from
    apyhiveapi==1.0.9's real installed source (HiveSession.login/
    startSession/updateTokens), not invented."""

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
        # Mirrors the real library's own startSession/updateTokens exactly,
        # including the real (surprising) behaviour that caught the
        # production bug this test suite exists to catch: a "tokens" key
        # present but with blank "token"/"accessToken" placeholders (the
        # shape _tokens_config/_resume_config build for the *resume* path)
        # unconditionally OVERWRITES tokenData's existing token/accessToken
        # with those blanks (see HiveSession.updateTokens's "elif 'token' in
        # tokens" branch) -- it does not merge or skip blanks. If production
        # code ever again routes a fresh-login's real tokens back through
        # this method with that placeholder shape, this fake will actually
        # clobber them here too, the same way the real library does.
        self.start_session_configs.append(config)
        if config and "tokens" in config:
            self.tokens.tokenData["token"] = config["tokens"]["token"]
            self.tokens.tokenData["refreshToken"] = config["tokens"]["refreshToken"]
            self.tokens.tokenData["accessToken"] = config["tokens"]["accessToken"]

    async def getDevices(self, _n_id: str) -> None:
        self.get_devices_call_count += 1


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
    # Devices are populated via a direct getDevices() call, NOT by routing
    # back through startSession() -- see _populate_devices's docstring for
    # why. Asserting this negatively (start_session_configs is empty) is as
    # important as the positive getDevices assertion below: it's exactly
    # what proves the real tokens login() just obtained were never handed
    # back to startSession()'s config-driven updateTokens() and clobbered.
    assert not fake_hive.start_session_configs
    assert fake_hive.get_devices_call_count == 1
    # The regression this test suite exists to catch: login()'s real tokens
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

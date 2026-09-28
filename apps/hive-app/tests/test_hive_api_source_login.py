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
        self.start_session_configs: list[dict[str, Any] | None] = []
        # Records call order, not just counts -- proves login() genuinely
        # precedes the follow-up startSession() (and its own getDevices()/
        # createDevices() tail) rather than just each being called once in
        # an unverified order. Call counts are read back via .count(...).
        self.call_order: list[str] = []

    async def login(self) -> dict[str, Any]:
        self.call_order.append("login")
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
        self.call_order.append("startSession")
        if config != {} and "tokens" in config:
            self.tokens.tokenData["token"] = config["tokens"]["token"]
            self.tokens.tokenData["refreshToken"] = config["tokens"]["refreshToken"]
            self.tokens.tokenData["accessToken"] = config["tokens"]["accessToken"]
        await self.getDevices("No_ID")
        await self.createDevices()

    async def getDevices(self, _n_id: str) -> None:
        self.call_order.append("getDevices")

    async def createDevices(self) -> dict[str, Any]:
        # Real createDevices() builds deviceList from whatever getDevices()
        # populated on self.data -- simplified here to a single climate
        # device, enough to prove HiveApiSource's own _climate_device
        # (which reads hive.deviceList.get("climate", [])) would find one.
        self.call_order.append("createDevices")
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

    # Proves login() genuinely precedes the follow-up startSession() call
    # (and its own getDevices()/createDevices() tail) -- counts alone would
    # pass even if a regression reversed the order, since this double's
    # empty-config startSession() doesn't require tokens to already be set.
    assert fake_hive.call_order == [
        "login",
        "startSession",
        "getDevices",
        "createDevices",
    ]
    # The follow-up startSession() call is passed an explicitly empty
    # config -- proving the real tokens login() just obtained are never
    # handed back through startSession()'s token-processing block (which
    # would clobber them, see _FakeApyHive.startSession's own comment).
    assert fake_hive.start_session_configs == [{}]
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
    assert fake_hive.call_order == ["login"]
    assert not fake_hive.start_session_configs


def test_a_fresh_login_with_an_unrecognised_result_raises_rather_than_silently_succeeding(
    mariadb_client: MariaDBClient, monkeypatch: Any
) -> None:
    # A result with neither "AuthenticationResult" nor the SMS_MFA challenge
    # -- e.g. an unrecognised/future challenge type -- must not silently
    # fall through: without an explicit raise here, this method would
    # return None, and the caller would persist a HiveAuthState with an
    # empty refresh_token as though login had succeeded, deferring the
    # failure to a later, harder-to-diagnose resume attempt.
    login_result = {"ChallengeName": "SOME_FUTURE_CHALLENGE"}
    fake_hive = _FakeApyHive(login_result)
    monkeypatch.setattr("hive_app.data.hive_client.Hive", lambda **kwargs: fake_hive)

    with pytest.raises(RuntimeError):
        HiveApiSource(_hive_settings(), mariadb_client).login()

    assert fake_hive.call_order == ["login"]
    assert not fake_hive.start_session_configs

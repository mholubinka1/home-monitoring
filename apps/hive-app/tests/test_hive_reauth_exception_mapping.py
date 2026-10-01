from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import responses
from apyhiveapi.helper import hive_exceptions

from hive_app.common.config import HiveSettings
from hive_app.common.exceptions import HiveReauthRequired
from hive_app.data.heating import HeatingRetriever
from hive_app.data.hive_client import _LOGIN_REQUIRES_SMS_MESSAGE, HiveApiSource
from hive_app.data.model import HiveAuthState
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.notify import NtfyReauthNotifier


class _RaisingApyHive:
    """Stands in for apyhiveapi's real `Hive` object -- the system boundary
    -- with login()/startSession() raising whichever apyhiveapi exception is
    under test, as the real library does when Cognito rejects a session."""

    def __init__(self, raises: Exception, **_: Any) -> None:
        self._raises = raises
        self.tokens = SimpleNamespace(tokenData={})
        self.auth = SimpleNamespace(SMS_MFA_CHALLENGE="SMS_MFA")

    async def login(self) -> dict[str, Any]:
        raise self._raises

    async def startSession(self, config: dict[str, Any] | None = None) -> None:
        raise self._raises


def _hive_settings() -> HiveSettings:
    return HiveSettings(username="user@example.com", password="hunter2")


def _persisted_state() -> HiveAuthState:
    return HiveAuthState(
        refresh_token="refresh-tok",
        device_group_key="group",
        device_key="device",
        device_password="password",
        updated_at=datetime.now(UTC),
    )


# Every apyhiveapi exception that means "a live human must redo SMS 2FA login".
_NEEDS_LIVE_RELOGIN = [
    hive_exceptions.HiveReauthRequired(),
    hive_exceptions.HiveInvalidDeviceAuthentication(),
    hive_exceptions.HiveAuthError(),
    hive_exceptions.HiveFailedToRefreshTokens(),
    hive_exceptions.HiveInvalid2FACode(),
    hive_exceptions.HiveUnknownConfiguration(),
]
# Config-time credential errors: a different problem, never the re-login alert.
_CREDENTIAL_ERRORS = [
    hive_exceptions.HiveInvalidUsername(),
    hive_exceptions.HiveInvalidPassword(),
]


def _exception_id(exception: Exception) -> str:
    return type(exception).__name__


def _install_hive_raising(monkeypatch: Any, exception: Exception) -> None:
    fake_hive = _RaisingApyHive(exception)
    monkeypatch.setattr("hive_app.data.hive_client.Hive", lambda **kwargs: fake_hive)


@pytest.mark.parametrize("exception", _NEEDS_LIVE_RELOGIN, ids=_exception_id)
def test_resuming_a_session_that_needs_a_live_relogin_requires_reauth(
    mariadb_client: MariaDBClient, monkeypatch: Any, exception: Exception
) -> None:
    _install_hive_raising(monkeypatch, exception)

    with pytest.raises(HiveReauthRequired):
        HiveApiSource(_hive_settings(), mariadb_client).resume(_persisted_state())


@pytest.mark.parametrize("exception", _NEEDS_LIVE_RELOGIN, ids=_exception_id)
def test_fresh_login_that_needs_a_live_relogin_requires_sms_reauth(
    mariadb_client: MariaDBClient, monkeypatch: Any, exception: Exception
) -> None:
    _install_hive_raising(monkeypatch, exception)

    with pytest.raises(HiveReauthRequired) as raised:
        HiveApiSource(_hive_settings(), mariadb_client).login()

    assert str(raised.value) == _LOGIN_REQUIRES_SMS_MESSAGE


@pytest.mark.parametrize("exception", _CREDENTIAL_ERRORS, ids=_exception_id)
@pytest.mark.parametrize("entry_point", ["login", "resume"])
def test_credential_errors_are_not_treated_as_needing_a_live_relogin(
    mariadb_client: MariaDBClient,
    monkeypatch: Any,
    exception: Exception,
    entry_point: str,
) -> None:
    _install_hive_raising(monkeypatch, exception)
    source = HiveApiSource(_hive_settings(), mariadb_client)

    with pytest.raises(type(exception)):
        if entry_point == "login":
            source.login()
        else:
            source.resume(_persisted_state())


@responses.activate
def test_unrecognised_device_during_a_poll_sends_one_reauth_alert(
    mariadb_client: MariaDBClient, monkeypatch: Any, tmp_path: Path
) -> None:
    topic_url = "https://ntfy.sh/hive-app-reauth-alerts"
    responses.add(responses.POST, topic_url, status=200)
    settings = HiveSettings(
        username="user@example.com",
        password="hunter2",
        auth_state_path=str(tmp_path / "hive_auth_state.json"),
    )
    source = HiveApiSource(settings, mariadb_client)
    source.persist_auth_state(_persisted_state())
    _install_hive_raising(
        monkeypatch, hive_exceptions.HiveInvalidDeviceAuthentication()
    )
    heating = HeatingRetriever(source, NtfyReauthNotifier(topic_url))

    with pytest.raises(HiveReauthRequired):
        heating.refresh()

    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == topic_url

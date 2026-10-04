from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import responses
from apyhiveapi.helper import hive_exceptions
from schedule import Scheduler

from hive_app.common.config import HiveSettings
from hive_app.common.exceptions import HiveApiUnavailable, HiveReauthRequired
from hive_app.data.heating import HeatingRetriever
from hive_app.data.hive_client import _LOGIN_REQUIRES_SMS_MESSAGE, HiveApiSource
from hive_app.data.model import HiveAuthState
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.notify import NtfyReauthNotifier, ReauthAlert
from hive_app.main import register_heating_refresh_job


class _RaisingApyHive:
    """Stands in for apyhiveapi's real `Hive` object -- the system boundary
    -- with login()/startSession() raising whichever apyhiveapi exception is
    under test, as the real library does when Cognito rejects a session."""

    def __init__(self, raises: Exception, **_: Any) -> None:
        self._raises = raises
        self.tokens = SimpleNamespace(tokenData={})
        self.auth = SimpleNamespace(SMS_MFA_CHALLENGE="SMS_MFA")
        self.api = SimpleNamespace(getAll=self._get_all)

    async def _get_all(self) -> dict[str, Any]:
        return {}

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
    topic_url = "https://ntfy.sh/home-monitoring-hive-auth-ntfy-test"
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
    heating = HeatingRetriever(source, ReauthAlert(NtfyReauthNotifier(topic_url)))

    with pytest.raises(HiveReauthRequired):
        heating.refresh()

    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == topic_url


class _TimingOutApyHive:
    """Stands in for apyhiveapi's `Hive` as it behaves when Hive's API times
    out: getAll() raises asyncio.TimeoutError, the library's own getDevices
    swallows it, and startSession then raises HiveReauthRequired because a
    fresh Hive has no devices."""

    def __init__(self, **_: Any) -> None:
        self.tokens = SimpleNamespace(tokenData={})
        self.auth = SimpleNamespace(SMS_MFA_CHALLENGE="SMS_MFA")
        self.api = SimpleNamespace(getAll=self._get_all)

    async def _get_all(self) -> dict[str, Any]:
        raise TimeoutError

    async def login(self) -> dict[str, Any]:
        return {"AuthenticationResult": {}}

    async def startSession(self, _config: dict[str, Any] | None = None) -> None:
        try:
            await self.api.getAll()
        except TimeoutError:
            pass
        raise hive_exceptions.HiveReauthRequired()


@responses.activate
def test_a_hive_api_timeout_during_a_poll_is_not_a_reauth_alert(
    mariadb_client: MariaDBClient, monkeypatch: Any, tmp_path: Path
) -> None:
    topic_url = "https://ntfy.sh/home-monitoring-hive-auth-ntfy-test"
    responses.add(responses.POST, topic_url, status=200)
    settings = HiveSettings(
        username="user@example.com",
        password="hunter2",
        auth_state_path=str(tmp_path / "hive_auth_state.json"),
    )
    source = HiveApiSource(settings, mariadb_client)
    source.persist_auth_state(_persisted_state())
    monkeypatch.setattr("hive_app.data.hive_client.Hive", _TimingOutApyHive)
    heating = HeatingRetriever(source, ReauthAlert(NtfyReauthNotifier(topic_url)))

    with pytest.raises(HiveApiUnavailable):
        heating.refresh()

    assert len(responses.calls) == 0


def _polling_heating(mariadb_client: MariaDBClient, tmp_path: Path) -> HeatingRetriever:
    settings = HiveSettings(
        username="user@example.com",
        password="hunter2",
        auth_state_path=str(tmp_path / "hive_auth_state.json"),
    )
    source = HiveApiSource(settings, mariadb_client)
    source.persist_auth_state(_persisted_state())
    return HeatingRetriever(
        source,
        ReauthAlert(
            NtfyReauthNotifier("https://ntfy.sh/home-monitoring-hive-auth-ntfy-test")
        ),
    )


@responses.activate
def test_a_genuine_reauth_requirement_during_a_poll_still_sends_one_alert(
    mariadb_client: MariaDBClient, monkeypatch: Any, tmp_path: Path
) -> None:
    responses.add(
        responses.POST,
        "https://ntfy.sh/home-monitoring-hive-auth-ntfy-test",
        status=200,
    )
    _install_hive_raising(monkeypatch, hive_exceptions.HiveReauthRequired())
    heating = _polling_heating(mariadb_client, tmp_path)

    with pytest.raises(HiveReauthRequired):
        heating.refresh()

    assert len(responses.calls) == 1


@responses.activate
def test_a_hive_api_timeout_does_not_taint_a_later_genuine_reauth_requirement(
    mariadb_client: MariaDBClient, monkeypatch: Any, tmp_path: Path
) -> None:
    responses.add(
        responses.POST,
        "https://ntfy.sh/home-monitoring-hive-auth-ntfy-test",
        status=200,
    )
    hives: list[Any] = [
        _TimingOutApyHive(),
        _RaisingApyHive(hive_exceptions.HiveReauthRequired()),
    ]
    monkeypatch.setattr("hive_app.data.hive_client.Hive", lambda **kwargs: hives.pop(0))
    heating = _polling_heating(mariadb_client, tmp_path)

    with pytest.raises(HiveApiUnavailable):
        heating.refresh()
    with pytest.raises(HiveReauthRequired):
        heating.refresh()

    assert len(responses.calls) == 1


@responses.activate
@pytest.mark.parametrize("entry_point", ["login", "resume"])
def test_a_hive_api_timeout_during_startup_authentication_is_not_a_reauth(
    mariadb_client: MariaDBClient, monkeypatch: Any, entry_point: str
) -> None:
    monkeypatch.setattr("hive_app.data.hive_client.Hive", _TimingOutApyHive)
    source = HiveApiSource(_hive_settings(), mariadb_client)

    with pytest.raises(HiveApiUnavailable):
        if entry_point == "login":
            source.login()
        else:
            source.resume(_persisted_state())

    assert len(responses.calls) == 0


@responses.activate
def test_a_scheduled_poll_hitting_a_hive_api_timeout_is_retried_without_alerting(
    mariadb_client: MariaDBClient, monkeypatch: Any, tmp_path: Path
) -> None:
    sleep_delays: list[int] = []
    monkeypatch.setattr("hive_app.common.decorator.time.sleep", sleep_delays.append)
    monkeypatch.setattr("hive_app.data.hive_client.Hive", _TimingOutApyHive)
    heating = _polling_heating(mariadb_client, tmp_path)

    job = register_heating_refresh_job(Scheduler(), heating, mariadb_client)
    job.run().join()

    assert sleep_delays == [60, 120, 240, 480]
    assert len(responses.calls) == 0
    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()
    assert len(runs) == 5
    assert all(run.status == "failure" for run in runs)

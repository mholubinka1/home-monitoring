from datetime import UTC, datetime

import pytest

from hive_app.common.exceptions import HiveReauthRequired
from hive_app.data.auth import HiveAuthenticator
from hive_app.data.model import HeatingStatus, HiveAuthState
from hive_app.data.notify import ReauthAlert


class _FakeAuthHiveSource:
    """A fake HiveSource for auth-branching tests: read_auth_state/
    persist_auth_state are backed by a plain in-memory instance attribute
    (mirroring HiveApiSource's real file-based storage, without touching a
    real file), while login()/resume() are recorded rather than performing
    any real Cognito SRP work -- same spirit as _FakeHiveSource in
    test_heating_retrieval.py."""

    def __init__(self, initial_state: HiveAuthState | None = None) -> None:
        self._state = initial_state
        self.login_called = False
        self.resume_called_with: HiveAuthState | None = None

    def fetch_heating_status(self) -> HeatingStatus:
        raise AssertionError("fetch_heating_status should never be reached")

    def persist_heating_status(self, status: HeatingStatus) -> None:
        raise AssertionError("persist_heating_status should never be reached")

    def read_auth_state(self) -> HiveAuthState | None:
        return self._state

    def login(self) -> HiveAuthState:
        self.login_called = True
        return HiveAuthState(
            refresh_token="fresh-refresh-token",
            device_group_key="fresh-device-group-key",
            device_key="fresh-device-key",
            device_password="fresh-device-password",
            updated_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        )

    def resume(self, state: HiveAuthState) -> HiveAuthState:
        self.resume_called_with = state
        return state

    def persist_auth_state(self, state: HiveAuthState) -> None:
        self._state = state


def test_no_existing_auth_state_takes_the_interactive_login_path() -> None:
    source = _FakeAuthHiveSource()

    HiveAuthenticator(source).authenticate()

    assert source.login_called is True
    assert source.resume_called_with is None

    stored = source.read_auth_state()
    assert stored is not None
    assert stored.refresh_token == "fresh-refresh-token"
    assert stored.device_group_key == "fresh-device-group-key"
    assert stored.device_key == "fresh-device-key"


class _ReauthRequiredAuthHiveSource(_FakeAuthHiveSource):
    def resume(self, state: HiveAuthState) -> HiveAuthState:
        raise HiveReauthRequired("Hive's remembered device is no longer recognized.")


class _SpyReauthNotifier:
    def __init__(self) -> None:
        self.calls = 0

    def notify_reauth_required(self) -> None:
        self.calls += 1

    def notify_auth_recovered(self) -> None:
        pass


def test_startup_notifies_immediately_when_resuming_needs_a_live_relogin() -> None:
    existing_state = HiveAuthState(
        refresh_token="existing-refresh-token",
        device_group_key="existing-device-group-key",
        device_key="existing-device-key",
        device_password="existing-device-password",
        updated_at=datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
    )
    source = _ReauthRequiredAuthHiveSource(initial_state=existing_state)
    notifier = _SpyReauthNotifier()
    authenticator = HiveAuthenticator(source, ReauthAlert(notifier))

    with pytest.raises(HiveReauthRequired):
        authenticator.authenticate()

    assert notifier.calls == 1


class _LoginNeedsLiveSmsAuthHiveSource(_FakeAuthHiveSource):
    def login(self) -> HiveAuthState:
        raise HiveReauthRequired("Hive login requires a live SMS 2FA code.")


def test_startup_notifies_when_a_first_ever_login_needs_a_live_sms_code() -> None:
    source = _LoginNeedsLiveSmsAuthHiveSource()
    notifier = _SpyReauthNotifier()
    authenticator = HiveAuthenticator(source, ReauthAlert(notifier))

    with pytest.raises(HiveReauthRequired):
        authenticator.authenticate()

    assert notifier.calls == 1
    assert source.read_auth_state() is None


class _NotifierThatFailsOnce:
    def __init__(self) -> None:
        self.calls = 0

    def notify_reauth_required(self) -> None:
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("ntfy.sh unreachable")

    def notify_auth_recovered(self) -> None:
        pass


def test_startup_retries_notifying_after_a_failed_delivery_attempt() -> None:
    source = _ReauthRequiredAuthHiveSource(
        initial_state=HiveAuthState(
            refresh_token="existing-refresh-token",
            device_group_key="existing-device-group-key",
            device_key="existing-device-key",
            device_password="existing-device-password",
            updated_at=datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
        )
    )
    notifier = _NotifierThatFailsOnce()
    authenticator = HiveAuthenticator(source, ReauthAlert(notifier))

    for _ in range(3):
        with pytest.raises(HiveReauthRequired):
            authenticator.authenticate()

    assert notifier.calls == 2


def test_existing_auth_state_takes_only_the_resume_path() -> None:
    existing_state = HiveAuthState(
        refresh_token="existing-refresh-token",
        device_group_key="existing-device-group-key",
        device_key="existing-device-key",
        device_password="existing-device-password",
        updated_at=datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
    )
    source = _FakeAuthHiveSource(initial_state=existing_state)

    HiveAuthenticator(source).authenticate()

    assert source.login_called is False
    assert source.resume_called_with == existing_state

    stored = source.read_auth_state()
    assert stored == existing_state

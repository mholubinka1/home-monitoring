from datetime import UTC, datetime

import pytest
import responses

from hive_app.common.exceptions import HiveReauthRequired
from hive_app.data.auth import HiveAuthenticator
from hive_app.data.heating import HeatingRetriever
from hive_app.data.model import HeatingStatus, HiveAuthState
from hive_app.data.notify import NtfyReauthNotifier, ReauthAlert
from hive_app.main import _build_reauth_alert, authenticate_at_startup

TOPIC_URL = "https://ntfy.sh/hive-app-reauth-alerts"
REQUIRED_TITLE = "hive-app: re-authentication required"
RECOVERED_TITLE = "hive-app: authentication recovered"


def _titles() -> list[str]:
    return [call.request.headers["Title"] for call in responses.calls]


_PERSISTED_STATE = HiveAuthState(
    refresh_token="existing-refresh-token",
    device_group_key="existing-device-group-key",
    device_key="existing-device-key",
    device_password="existing-device-password",
    updated_at=datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
)


class _ReauthRequiredHiveSource:
    """A fake HiveSource with a persisted auth state whose resume and poll
    both need a live SMS re-login -- the shared incident that startup auth
    and the heating poll each observe."""

    def fetch_heating_status(self) -> HeatingStatus:
        raise HiveReauthRequired("Hive's remembered device is no longer recognized.")

    def persist_heating_status(self, status: HeatingStatus) -> None:
        raise AssertionError("persist_heating_status should never be reached")

    def read_auth_state(self) -> HiveAuthState | None:
        return _PERSISTED_STATE

    def login(self) -> HiveAuthState:
        raise AssertionError("login should never be reached")

    def resume(self, state: HiveAuthState) -> HiveAuthState:
        raise HiveReauthRequired("Hive's remembered device is no longer recognized.")

    def persist_auth_state(self, state: HiveAuthState) -> None:
        raise AssertionError("persist_auth_state should never be reached")


@responses.activate
def test_a_reauth_incident_seen_at_startup_and_then_by_the_poll_alerts_once() -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    source = _ReauthRequiredHiveSource()
    alert = ReauthAlert(NtfyReauthNotifier(TOPIC_URL))
    authenticator = HiveAuthenticator(source, alert)
    heating = HeatingRetriever(source, alert)

    with pytest.raises(HiveReauthRequired):
        authenticator.authenticate()
    with pytest.raises(HiveReauthRequired):
        heating.refresh()

    assert len(responses.calls) == 1


class _RecoverableHiveSource(_ReauthRequiredHiveSource):
    """Same incident as above, but flips to healthy once `recovered` is set."""

    def __init__(self) -> None:
        self.recovered = False

    def fetch_heating_status(self) -> HeatingStatus:
        if not self.recovered:
            return super().fetch_heating_status()
        return HeatingStatus(
            polled_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
            current_temp=19.5,
            target_temp=21.0,
            mode="SCHEDULE",
            state="ON",
            boost_active=False,
            boost_ends_at=None,
            schedule={"now": "21.0", "next": "18.0", "later": "19.0"},
        )

    def persist_heating_status(self, status: HeatingStatus) -> None:
        pass

    def resume(self, state: HiveAuthState) -> HiveAuthState:
        if not self.recovered:
            return super().resume(state)
        return state

    def persist_auth_state(self, state: HiveAuthState) -> None:
        pass


@responses.activate
def test_a_successful_poll_after_an_alert_lets_a_later_incident_alert_again() -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    source = _RecoverableHiveSource()
    alert = ReauthAlert(NtfyReauthNotifier(TOPIC_URL))
    authenticator = HiveAuthenticator(source, alert)
    heating = HeatingRetriever(source, alert)

    with pytest.raises(HiveReauthRequired):
        authenticator.authenticate()
    source.recovered = True
    heating.refresh()
    source.recovered = False
    with pytest.raises(HiveReauthRequired):
        authenticator.authenticate()

    assert _titles() == [REQUIRED_TITLE, RECOVERED_TITLE, REQUIRED_TITLE]


@responses.activate
def test_a_successful_startup_after_an_alert_lets_a_later_incident_alert_again() -> (
    None
):
    responses.add(responses.POST, TOPIC_URL, status=200)
    source = _RecoverableHiveSource()
    alert = ReauthAlert(NtfyReauthNotifier(TOPIC_URL))
    authenticator = HiveAuthenticator(source, alert)
    heating = HeatingRetriever(source, alert)

    with pytest.raises(HiveReauthRequired):
        heating.refresh()
    source.recovered = True
    authenticator.authenticate()
    source.recovered = False
    with pytest.raises(HiveReauthRequired):
        heating.refresh()

    assert _titles() == [REQUIRED_TITLE, RECOVERED_TITLE, REQUIRED_TITLE]


class _PersistRecordingHiveSource(_RecoverableHiveSource):
    def __init__(self) -> None:
        super().__init__()
        self.persisted: list[HeatingStatus] = []

    def persist_heating_status(self, status: HeatingStatus) -> None:
        self.persisted.append(status)


@responses.activate
def test_a_failed_recovered_notice_does_not_stop_the_poll_persisting_status() -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    source = _PersistRecordingHiveSource()
    alert = ReauthAlert(NtfyReauthNotifier(TOPIC_URL))
    heating = HeatingRetriever(source, alert)

    with pytest.raises(HiveReauthRequired):
        heating.refresh()
    source.recovered = True
    responses.replace(responses.POST, TOPIC_URL, status=500)
    heating.refresh()

    assert len(source.persisted) == 1
    assert _titles() == [REQUIRED_TITLE, RECOVERED_TITLE]


class _UnreachableHiveSource(_ReauthRequiredHiveSource):
    def resume(self, state: HiveAuthState) -> HiveAuthState:
        raise ConnectionError("Hive backend unreachable")


@responses.activate
def test_startup_failure_that_is_not_a_reauth_incident_is_logged_without_alerting() -> (
    None
):
    alert = ReauthAlert(NtfyReauthNotifier(TOPIC_URL))
    authenticator = HiveAuthenticator(_UnreachableHiveSource(), alert)

    authenticate_at_startup(authenticator)

    assert len(responses.calls) == 0


@responses.activate
def test_startup_reauth_incident_sends_one_alert_and_does_not_crash_startup() -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    alert = _build_reauth_alert(TOPIC_URL)
    authenticator = HiveAuthenticator(_ReauthRequiredHiveSource(), alert)

    authenticate_at_startup(authenticator)

    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == TOPIC_URL


@responses.activate
def test_reauth_incident_without_ntfy_configured_makes_no_http_call() -> None:
    # No responses registered: an unexpected HTTP call would raise
    # ConnectionError here instead of the HiveReauthRequired asserted.
    alert = _build_reauth_alert(None)
    authenticator = HiveAuthenticator(_ReauthRequiredHiveSource(), alert)

    with pytest.raises(HiveReauthRequired):
        authenticator.authenticate()

    assert len(responses.calls) == 0

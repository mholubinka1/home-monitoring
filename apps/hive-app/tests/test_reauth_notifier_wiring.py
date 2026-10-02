import pytest
import responses

from hive_app.common.exceptions import HiveReauthRequired
from hive_app.data.heating import HeatingRetriever
from hive_app.data.model import HeatingStatus, HiveAuthState
from hive_app.main import _build_reauth_alert, _build_reauth_notifier

TOPIC_URL = "https://ntfy.sh/home-monitoring-hive-auth-ntfy-test"


@responses.activate
def test_build_reauth_notifier_posts_to_the_configured_topic_url_when_set() -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)

    notifier = _build_reauth_notifier(TOPIC_URL)
    assert notifier is not None
    notifier.notify_reauth_required()

    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == TOPIC_URL


def test_build_reauth_notifier_returns_none_when_topic_url_is_omitted() -> None:
    assert _build_reauth_notifier(None) is None


def test_build_reauth_notifier_returns_none_when_topic_url_is_an_empty_string() -> None:
    assert _build_reauth_notifier("") is None


class _ReauthRequiredHiveSource:
    """A fake HiveSource whose poll always raises the unrecoverable reauth
    error -- used to observe, black-box, whether a HeatingRetriever built by
    _build_reauth_alert() actually notifies via the wired-up notifier."""

    def fetch_heating_status(self) -> HeatingStatus:
        raise HiveReauthRequired("Hive's remembered device is no longer recognized.")

    def persist_heating_status(self, status: HeatingStatus) -> None:
        raise AssertionError("persist_heating_status should never be reached")

    def read_auth_state(self) -> HiveAuthState | None:
        raise NotImplementedError

    def login(self) -> HiveAuthState:
        raise NotImplementedError

    def resume(self, state: HiveAuthState) -> HiveAuthState:
        raise NotImplementedError

    def persist_auth_state(self, state: HiveAuthState) -> None:
        raise NotImplementedError


@responses.activate
def test_build_reauth_alert_wires_a_configured_ntfy_topic_through_to_a_notify() -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    heating = HeatingRetriever(
        _ReauthRequiredHiveSource(), _build_reauth_alert(TOPIC_URL)
    )

    with pytest.raises(HiveReauthRequired):
        heating.refresh()

    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == TOPIC_URL


@responses.activate
def test_build_reauth_alert_with_no_ntfy_settings_attempts_no_http_call() -> None:
    # No responses registered: an unexpected HTTP call from a regressed
    # wiring (e.g. a hardcoded notifier) would raise ConnectionError here
    # instead of the plain HiveReauthRequired this asserts.
    heating = HeatingRetriever(_ReauthRequiredHiveSource(), _build_reauth_alert(None))

    with pytest.raises(HiveReauthRequired):
        heating.refresh()

    assert len(responses.calls) == 0

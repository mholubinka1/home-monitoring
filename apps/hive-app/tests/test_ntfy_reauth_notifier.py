import pytest
import requests
import responses

from hive_app.data.notify import NtfyReauthNotifier

TOPIC_URL = "https://ntfy.sh/hive-app-reauth-alerts"


@responses.activate
def test_notify_reauth_required_posts_a_plain_text_message_to_the_ntfy_topic() -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)

    NtfyReauthNotifier(TOPIC_URL).notify_reauth_required()

    assert len(responses.calls) == 1
    assert responses.calls[0].request.url == TOPIC_URL
    assert responses.calls[0].request.body is not None


RUNBOOK_URL = (
    "https://github.com/mholubinka1/home-monitoring/blob/main/"
    "deployments/hive-app/REAUTH_RUNBOOK.md"
)


@responses.activate
def test_the_reauth_required_alert_is_high_priority_and_links_to_the_runbook() -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)

    NtfyReauthNotifier(TOPIC_URL).notify_reauth_required()

    request = responses.calls[0].request
    assert request.headers["Title"] == "hive-app: re-authentication required"
    assert request.headers["Priority"] == "high"
    assert request.headers["Tags"] == "warning,key"
    assert request.headers["Click"] == RUNBOOK_URL
    assert request.body == (
        b"Hive needs a live SMS 2FA code to recover. See the runbook."
    )


@responses.activate
def test_the_auth_recovered_notification_is_default_priority_with_no_click_link() -> (
    None
):
    responses.add(responses.POST, TOPIC_URL, status=200)

    NtfyReauthNotifier(TOPIC_URL).notify_auth_recovered()

    request = responses.calls[0].request
    assert request.url == TOPIC_URL
    assert request.headers["Title"] == "hive-app: authentication recovered"
    assert request.headers["Priority"] == "default"
    assert request.headers["Tags"] == "white_check_mark"
    assert "Click" not in request.headers
    assert request.body == b"Hive login restored. Heating polling has resumed."


@responses.activate
def test_notify_auth_recovered_raises_when_ntfy_responds_with_an_error() -> None:
    responses.add(responses.POST, TOPIC_URL, status=500)

    with pytest.raises(requests.HTTPError):
        NtfyReauthNotifier(TOPIC_URL).notify_auth_recovered()


@responses.activate
def test_notify_reauth_required_raises_when_ntfy_responds_with_an_error() -> None:
    responses.add(responses.POST, TOPIC_URL, status=500)

    with pytest.raises(requests.HTTPError):
        NtfyReauthNotifier(TOPIC_URL).notify_reauth_required()

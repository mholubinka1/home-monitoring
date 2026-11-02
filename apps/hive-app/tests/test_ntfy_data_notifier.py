from datetime import timedelta

import pytest
import requests
import responses

from hive_app.data.notify import RUNBOOK_URL, NtfyDataNotifier, describe_duration

TOPIC_URL = "https://ntfy.sh/home-monitoring-hive-data-ntfy-test"


@responses.activate
def test_data_lost_is_a_high_priority_message_linking_to_the_runbook() -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)

    NtfyDataNotifier(TOPIC_URL).notify_data_lost(
        timedelta(minutes=31), "ConnectionError"
    )

    request = responses.calls[0].request
    assert request.url == TOPIC_URL
    assert request.headers["Title"] == "hive-app: Hive data lost"
    assert request.headers["Priority"] == "high"
    assert request.headers["Tags"] == "warning"
    assert request.headers["Click"] == RUNBOOK_URL
    assert request.body == (
        b"No Hive heating data for 31 minutes. Latest poll failure: ConnectionError."
    )


@responses.activate
def test_a_data_lost_reminder_is_titled_still_lost_and_omits_an_unknown_cause() -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)

    NtfyDataNotifier(TOPIC_URL).notify_data_lost(
        timedelta(hours=1, minutes=31), None, reminder=True
    )

    request = responses.calls[0].request
    assert request.headers["Title"] == "hive-app: Hive data still lost"
    assert request.body == b"No Hive heating data for 91 minutes."


@responses.activate
def test_data_restored_is_default_priority_with_no_click_link() -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)

    NtfyDataNotifier(TOPIC_URL).notify_data_restored(timedelta(minutes=45))

    request = responses.calls[0].request
    assert request.headers["Title"] == "hive-app: Hive data restored"
    assert request.headers["Priority"] == "default"
    assert request.headers["Tags"] == "white_check_mark"
    assert "Click" not in request.headers
    assert request.body == b"Hive heating data is arriving again after 45 minutes."


@responses.activate
def test_a_delivery_error_from_ntfy_is_raised() -> None:
    responses.add(responses.POST, TOPIC_URL, status=500)

    with pytest.raises(requests.HTTPError):
        NtfyDataNotifier(TOPIC_URL).notify_data_restored(timedelta(minutes=45))


@pytest.mark.parametrize(
    ("duration", "text"),
    [
        (timedelta(minutes=1), "1 minute"),
        (timedelta(minutes=119), "119 minutes"),
        (timedelta(hours=2), "2 hours 0 minutes"),
        (timedelta(hours=3, minutes=1), "3 hours 1 minute"),
        (timedelta(hours=48), "2 days 0 hours"),
        (timedelta(days=3, hours=1), "3 days 1 hour"),
    ],
)
def test_describe_duration_reads_naturally_at_each_scale(
    duration: timedelta, text: str
) -> None:
    assert describe_duration(duration) == text

import logging
from datetime import UTC, datetime, time, timedelta

import pytest
import responses

from hive_app.common.config import QuietHours
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.notify import NtfyDataNotifier
from hive_app.data.presence_watchdog import PresenceWatchdog, warn_if_house_empty

TOPIC_URL = "https://ntfy.sh/home-monitoring-presence-ntfy-test"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def _open_period(
    mariadb_client: MariaDBClient, state: str, started_at: datetime
) -> None:
    mariadb_client.change_presence_state(state, 0, started_at)


def _watchdog(mariadb_client: MariaDBClient) -> PresenceWatchdog:
    return PresenceWatchdog(mariadb_client, NtfyDataNotifier(TOPIC_URL))


@responses.activate
def test_presence_unknown_for_31_minutes_sends_a_signal_lost_alert(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _open_period(mariadb_client, "unknown", NOW - timedelta(minutes=31))

    _watchdog(mariadb_client).check(NOW)

    assert len(responses.calls) == 1
    request = responses.calls[0].request
    assert request.headers["Title"] == "hive-app: presence signal lost"
    assert request.headers["Priority"] == "high"
    assert request.body is not None
    assert "31 minutes" in request.body.decode("utf-8")


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, timedelta, bool]] = []
        self.failing = False

    def notify_presence_lost(self, age: timedelta, reminder: bool = False) -> None:
        if self.failing:
            raise RuntimeError("ntfy unreachable")
        self.sent.append(("lost", age, reminder))

    def notify_presence_restored(self, outage: timedelta) -> None:
        if self.failing:
            raise RuntimeError("ntfy unreachable")
        self.sent.append(("restored", outage, False))


def test_a_reminder_follows_an_hour_after_the_alert_and_not_earlier(
    mariadb_client: MariaDBClient,
) -> None:
    notifier = FakeNotifier()
    watchdog = PresenceWatchdog(mariadb_client, notifier)
    _open_period(mariadb_client, "unknown", NOW - timedelta(minutes=31))

    watchdog.check(NOW)
    watchdog.check(NOW + timedelta(minutes=59))
    assert [kind for kind, _, _ in notifier.sent] == ["lost"]
    watchdog.check(NOW + timedelta(minutes=60))

    assert [(kind, reminder) for kind, _, reminder in notifier.sent] == [
        ("lost", False),
        ("lost", True),
    ]


def test_presence_known_again_after_an_alert_sends_a_restored_message_with_the_outage(
    mariadb_client: MariaDBClient,
) -> None:
    notifier = FakeNotifier()
    watchdog = PresenceWatchdog(mariadb_client, notifier)
    _open_period(mariadb_client, "unknown", NOW - timedelta(minutes=31))
    watchdog.check(NOW)

    _open_period(mariadb_client, "someone_home", NOW + timedelta(minutes=10))
    watchdog.check(NOW + timedelta(minutes=11))
    watchdog.check(NOW + timedelta(minutes=12))

    assert notifier.sent[1:] == [("restored", timedelta(minutes=41), False)]


def test_no_restored_message_when_no_lost_alert_was_delivered(
    mariadb_client: MariaDBClient,
) -> None:
    notifier = FakeNotifier()
    watchdog = PresenceWatchdog(mariadb_client, notifier)
    _open_period(mariadb_client, "unknown", NOW - timedelta(minutes=5))
    watchdog.check(NOW)
    _open_period(mariadb_client, "no_one_home", NOW + timedelta(minutes=1))

    watchdog.check(NOW + timedelta(minutes=2))

    assert not notifier.sent


def test_nothing_is_sent_in_quiet_hours_until_they_end(
    mariadb_client: MariaDBClient,
) -> None:
    notifier = FakeNotifier()
    quiet = QuietHours(start=time(11, 0), end=time(13, 0))
    watchdog = PresenceWatchdog(mariadb_client, notifier, quiet)
    _open_period(mariadb_client, "unknown", NOW - timedelta(hours=3))

    watchdog.check(NOW)
    assert not notifier.sent
    watchdog.check(NOW + timedelta(hours=1))

    assert len(notifier.sent) == 1


def test_an_empty_house_at_the_night_check_logs_a_warning_with_its_duration(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    _open_period(mariadb_client, "no_one_home", NOW - timedelta(hours=5))

    with caplog.at_level(logging.WARNING):
        warn_if_house_empty(mariadb_client, NOW)

    assert "empty for 5 hours 0 minutes" in caplog.text


def test_someone_home_at_the_night_check_logs_no_warning(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    _open_period(mariadb_client, "someone_home", NOW - timedelta(hours=5))

    with caplog.at_level(logging.WARNING):
        warn_if_house_empty(mariadb_client, NOW)

    assert caplog.text == ""


def test_a_failed_lost_alert_is_retried_on_the_next_check(
    mariadb_client: MariaDBClient,
) -> None:
    notifier = FakeNotifier()
    watchdog = PresenceWatchdog(mariadb_client, notifier)
    _open_period(mariadb_client, "unknown", NOW - timedelta(minutes=31))

    notifier.failing = True
    watchdog.check(NOW)
    notifier.failing = False
    watchdog.check(NOW + timedelta(minutes=1))

    assert [(kind, reminder) for kind, _, reminder in notifier.sent] == [
        ("lost", False)
    ]


def test_a_failed_restored_message_is_retried_and_sent_once(
    mariadb_client: MariaDBClient,
) -> None:
    notifier = FakeNotifier()
    watchdog = PresenceWatchdog(mariadb_client, notifier)
    _open_period(mariadb_client, "unknown", NOW - timedelta(minutes=31))
    watchdog.check(NOW)
    _open_period(mariadb_client, "someone_home", NOW + timedelta(minutes=5))

    notifier.failing = True
    watchdog.check(NOW + timedelta(minutes=6))
    notifier.failing = False
    watchdog.check(NOW + timedelta(minutes=7))
    watchdog.check(NOW + timedelta(minutes=8))

    assert [kind for kind, _, _ in notifier.sent] == ["lost", "restored"]


def test_an_unseen_outage_end_is_reported_before_the_next_outage_alert(
    mariadb_client: MariaDBClient,
) -> None:
    notifier = FakeNotifier()
    watchdog = PresenceWatchdog(mariadb_client, notifier)
    _open_period(mariadb_client, "unknown", NOW - timedelta(minutes=31))
    watchdog.check(NOW)
    _open_period(mariadb_client, "someone_home", NOW + timedelta(minutes=5))
    _open_period(mariadb_client, "unknown", NOW + timedelta(minutes=10))

    watchdog.check(NOW + timedelta(minutes=45))

    assert [kind for kind, _, _ in notifier.sent] == ["lost", "restored", "lost"]

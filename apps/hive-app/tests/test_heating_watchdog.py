"""The Heating Unreachable alert: one ntfy alert when stored polls show Hive offline
or OFF for over 30 minutes, and one notice when it clears (spec:
bugfix-comfort-schedule-write-only-while-hive-online, issue #828).

Driven through HeatingWatchdog.check(now) over polls stored with the real MariaDB
test client, with a fake notifier recording what would be sent."""

from datetime import UTC, datetime, time, timedelta

import responses

from hive_app.common.config import QuietHours
from hive_app.data.heating_watchdog import HeatingWatchdog
from hive_app.data.model import HeatingStatus
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.notify import NtfyDataNotifier

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
TOPIC_URL = "https://ntfy.sh/home-monitoring-heating-ntfy-test"


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, timedelta, str]] = []

    def notify_heating_unreachable(self, age: timedelta, reason: str) -> None:
        self.sent.append(("unreachable", age, reason))

    def notify_heating_reachable(self, outage: timedelta) -> None:
        self.sent.append(("reachable", outage, ""))


def _store_polls(  # pylint: disable=too-many-arguments
    mariadb: MariaDBClient,
    first: datetime,
    last: datetime,
    *,
    online: bool | None = True,
    mode: str = "SCHEDULE",
    step: timedelta = timedelta(minutes=2),
) -> None:
    """One stored poll every `step` (2 minutes) from first to last, inclusive."""
    polled_at = first
    while polled_at <= last:
        mariadb.write_heating_status(
            HeatingStatus(
                polled_at=polled_at,
                current_temp=18.0,
                target_temp=10.0,
                mode=mode,
                state="OFF",
                boost_active=False,
                boost_ends_at=None,
                schedule={},
                online=online,
            )
        )
        polled_at += step


def test_hive_offline_for_31_minutes_sends_one_alert_naming_the_reason(
    mariadb_client: MariaDBClient,
) -> None:
    # Given polls reachable until 31 minutes ago and offline since
    _store_polls(mariadb_client, NOW - timedelta(hours=1), NOW - timedelta(minutes=33))
    _store_polls(mariadb_client, NOW - timedelta(minutes=31), NOW, online=False)
    notifier = FakeNotifier()

    # When the watchdog checks
    HeatingWatchdog(mariadb_client, notifier).check(NOW)

    # Then one alert is sent, for 31 minutes, naming offline as the reason
    assert notifier.sent == [("unreachable", timedelta(minutes=31), "offline")]


def test_hive_unreachable_for_29_minutes_sends_no_alert(
    mariadb_client: MariaDBClient,
) -> None:
    # Given polls reachable until 31 minutes ago and OFF for the last 29 minutes
    _store_polls(mariadb_client, NOW - timedelta(hours=1), NOW - timedelta(minutes=31))
    _store_polls(mariadb_client, NOW - timedelta(minutes=29), NOW, mode="OFF")
    notifier = FakeNotifier()

    # When the watchdog checks
    HeatingWatchdog(mariadb_client, notifier).check(NOW)

    # Then no alert is sent
    assert not notifier.sent


def test_no_second_alert_is_sent_while_hive_stays_unreachable(
    mariadb_client: MariaDBClient,
) -> None:
    # Given an alert was sent for a spell offline since 31 minutes ago
    _store_polls(mariadb_client, NOW - timedelta(hours=1), NOW - timedelta(minutes=33))
    _store_polls(mariadb_client, NOW - timedelta(minutes=31), NOW, online=False)
    notifier = FakeNotifier()
    watchdog = HeatingWatchdog(mariadb_client, notifier)
    watchdog.check(NOW)

    # When polls stay offline and the watchdog checks again an hour later
    later = NOW + timedelta(hours=1)
    _store_polls(mariadb_client, NOW + timedelta(minutes=2), later, online=False)
    watchdog.check(later)

    # Then no second alert is sent
    assert [kind for kind, _, _ in notifier.sent] == ["unreachable"]


def test_a_spell_longer_than_the_window_is_not_alerted_again(
    mariadb_client: MariaDBClient,
) -> None:
    # Given an alert was sent for a spell offline since 31 minutes ago
    _store_polls(mariadb_client, NOW - timedelta(hours=1), NOW - timedelta(minutes=33))
    _store_polls(mariadb_client, NOW - timedelta(minutes=31), NOW, online=False)
    notifier = FakeNotifier()
    watchdog = HeatingWatchdog(mariadb_client, notifier)
    watchdog.check(NOW)

    # When it stays offline for 8 days (longer than the 7 days of polls read) and
    # the watchdog checks then
    later = NOW + timedelta(days=8)
    _store_polls(
        mariadb_client,
        NOW + timedelta(hours=1),
        later,
        online=False,
        step=timedelta(hours=1),
    )
    watchdog.check(later)

    # Then no second alert is sent
    assert [kind for kind, _, _ in notifier.sent] == ["unreachable"]


def test_one_reachable_notice_follows_an_alert_when_hive_is_back(
    mariadb_client: MariaDBClient,
) -> None:
    # Given an alert was sent for a spell offline since 31 minutes ago
    _store_polls(mariadb_client, NOW - timedelta(hours=1), NOW - timedelta(minutes=33))
    _store_polls(mariadb_client, NOW - timedelta(minutes=31), NOW, online=False)
    notifier = FakeNotifier()
    watchdog = HeatingWatchdog(mariadb_client, notifier)
    watchdog.check(NOW)

    # When a reachable poll is stored 10 minutes later and the watchdog checks,
    # twice
    back = NOW + timedelta(minutes=10)
    _store_polls(
        mariadb_client,
        NOW + timedelta(minutes=2),
        back - timedelta(minutes=2),
        online=False,
    )
    _store_polls(mariadb_client, back, back)
    watchdog.check(back)
    watchdog.check(back + timedelta(minutes=1))

    # Then one reachable notice is sent, with the spell's length: from its first
    # unreachable poll to the first reachable one after it
    assert notifier.sent[1:] == [("reachable", timedelta(minutes=41), "")]


def test_no_alert_is_sent_during_quiet_hours(
    mariadb_client: MariaDBClient,
) -> None:
    # Given an unreachable spell of 31 minutes, and quiet hours covering now
    _store_polls(mariadb_client, NOW - timedelta(hours=1), NOW - timedelta(minutes=33))
    _store_polls(mariadb_client, NOW - timedelta(minutes=31), NOW, online=False)
    notifier = FakeNotifier()
    quiet = QuietHours(start=time(11, 0), end=time(13, 0))

    # When the watchdog checks
    HeatingWatchdog(mariadb_client, notifier, quiet).check(NOW)

    # Then no alert is sent
    assert not notifier.sent


@responses.activate
def test_the_unreachable_alert_is_posted_to_ntfy_with_its_reason_and_age(
    mariadb_client: MariaDBClient,
) -> None:
    # Given Hive offline for 31 minutes and ntfy configured
    responses.add(responses.POST, TOPIC_URL, status=200)
    _store_polls(mariadb_client, NOW - timedelta(hours=1), NOW - timedelta(minutes=33))
    _store_polls(mariadb_client, NOW - timedelta(minutes=31), NOW, online=False)

    # When the watchdog checks
    HeatingWatchdog(mariadb_client, NtfyDataNotifier(TOPIC_URL)).check(NOW)

    # Then one high-priority ntfy message names the reason and how long
    assert len(responses.calls) == 1
    request = responses.calls[0].request
    assert request.headers["Title"] == "hive-app: Hive heating unreachable"
    assert request.headers["Priority"] == "high"
    assert request.body is not None
    body = request.body.decode("utf-8")
    assert "offline" in body
    assert "31 minutes" in body

from datetime import UTC, datetime, time, timedelta

import pytest
import responses

from common.local_day import set_local_timezone
from common.mariadb.model import job_run
from hive_app.common.config import QuietHours
from hive_app.data.model import HeatingStatus
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.notify import NtfyDataNotifier
from hive_app.data.watchdog import HiveDataWatchdog

TOPIC_URL = "https://ntfy.sh/home-monitoring-hive-auth-ntfy-test"
RUNBOOK_URL = (
    "https://github.com/mholubinka1/home-monitoring/blob/main/"
    "deployments/hive-app/REAUTH_RUNBOOK.md"
)
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _poll_at(mariadb_client: MariaDBClient, polled_at: datetime) -> None:
    mariadb_client.write_heating_status(
        HeatingStatus(
            polled_at=polled_at,
            current_temp=19.5,
            target_temp=21.0,
            mode="SCHEDULE",
            state="ON",
            boost_active=False,
            boost_ends_at=None,
            schedule={},
        )
    )


def _failed_poll(mariadb_client: MariaDBClient, ran_at: datetime, error: str) -> None:
    with mariadb_client.session_write_scope() as session:
        session.add(
            job_run(
                job_name="heating_refresh",
                status="failure",
                ran_at=ran_at.replace(tzinfo=None),
                error_message=error,
            )
        )


def _watchdog(
    mariadb_client: MariaDBClient, quiet_hours: QuietHours | None = None
) -> HiveDataWatchdog:
    return HiveDataWatchdog(mariadb_client, NtfyDataNotifier(TOPIC_URL), quiet_hours)


@responses.activate
def test_data_missing_for_31_minutes_sends_a_high_priority_lost_alert_with_the_cause(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, NOW - timedelta(minutes=31))
    _failed_poll(
        mariadb_client,
        NOW - timedelta(minutes=10),
        "'NoneType' object has no attribute 'get'",
    )

    _watchdog(mariadb_client).check(NOW)

    assert len(responses.calls) == 1
    request = responses.calls[0].request
    assert request.headers["Title"] == "hive-app: Hive data lost"
    assert request.headers["Priority"] == "high"
    assert request.headers["Tags"] == "warning"
    assert request.headers["Click"] == RUNBOOK_URL
    assert request.body is not None
    body = request.body.decode("utf-8")
    assert "31 minutes" in body
    assert "'NoneType' object has no attribute 'get'" in body


@responses.activate
def test_a_19_minute_gap_in_the_data_is_silent(
    mariadb_client: MariaDBClient,
) -> None:
    _poll_at(mariadb_client, NOW - timedelta(minutes=19))

    _watchdog(mariadb_client).check(NOW)

    assert len(responses.calls) == 0


@responses.activate
def test_a_brand_new_watchdog_mid_outage_alerts_straight_away_from_the_database_age(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, NOW - timedelta(hours=3))

    _watchdog(mariadb_client).check(NOW)

    assert len(responses.calls) == 1
    assert responses.calls[0].request.body is not None
    assert "3 hours 0 minutes" in responses.calls[0].request.body.decode("utf-8")


@responses.activate
def test_no_second_alert_while_the_data_is_still_missing(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, NOW - timedelta(minutes=31))
    watchdog = _watchdog(mariadb_client)

    watchdog.check(NOW)
    watchdog.check(NOW + timedelta(minutes=1))
    watchdog.check(NOW + timedelta(minutes=45))

    assert len(responses.calls) == 1


@responses.activate
def test_a_failed_delivery_is_retried_on_the_next_check(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=500)
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, NOW - timedelta(minutes=31))
    watchdog = _watchdog(mariadb_client)

    watchdog.check(NOW)
    watchdog.check(NOW + timedelta(minutes=1))
    watchdog.check(NOW + timedelta(minutes=2))

    assert len(responses.calls) == 2


@responses.activate
def test_restored_notice_gives_the_outage_length_after_a_delivered_lost_alert(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, NOW - timedelta(minutes=31))
    watchdog = _watchdog(mariadb_client)
    watchdog.check(NOW)
    _poll_at(mariadb_client, NOW + timedelta(minutes=90))

    watchdog.check(NOW + timedelta(minutes=91))

    assert len(responses.calls) == 2
    request = responses.calls[1].request
    assert request.headers["Title"] == "hive-app: Hive data restored"
    assert request.headers["Priority"] == "default"
    assert request.headers["Tags"] == "white_check_mark"
    assert "Click" not in request.headers
    assert request.body is not None
    assert "after 2 hours 1 minute." in request.body.decode("utf-8")


@responses.activate
def test_no_restored_notice_when_no_lost_alert_was_delivered(
    mariadb_client: MariaDBClient,
) -> None:
    _poll_at(mariadb_client, NOW - timedelta(minutes=5))

    _watchdog(mariadb_client).check(NOW)

    assert len(responses.calls) == 0


@responses.activate
def test_a_reminder_with_the_current_data_age_follows_an_hour_after_the_lost_alert(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, NOW - timedelta(minutes=31))
    watchdog = _watchdog(mariadb_client)
    watchdog.check(NOW)

    watchdog.check(NOW + timedelta(minutes=59))
    assert len(responses.calls) == 1

    watchdog.check(NOW + timedelta(minutes=60))
    assert len(responses.calls) == 2
    request = responses.calls[1].request
    assert request.headers["Title"] == "hive-app: Hive data still lost"
    assert request.headers["Priority"] == "high"
    assert request.body is not None
    assert "91 minutes" in request.body.decode("utf-8")


@responses.activate
def test_no_reminders_follow_once_the_data_has_returned(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, NOW - timedelta(minutes=31))
    watchdog = _watchdog(mariadb_client)
    watchdog.check(NOW)
    _poll_at(mariadb_client, NOW + timedelta(minutes=30))
    watchdog.check(NOW + timedelta(minutes=31))
    assert len(responses.calls) == 2

    _poll_at(mariadb_client, NOW + timedelta(minutes=60))
    watchdog.check(NOW + timedelta(minutes=61))
    _poll_at(mariadb_client, NOW + timedelta(minutes=120))
    watchdog.check(NOW + timedelta(minutes=121))

    assert len(responses.calls) == 2


# Local time equals UTC on these November dates (clocks went back on 25 Oct).
NIGHT = QuietHours(start=time(22, 0), end=time(8, 0))


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def _titles() -> list[str]:
    return [call.request.headers["Title"] for call in responses.calls]


@responses.activate
def test_an_outage_that_crosses_30_minutes_overnight_is_reported_at_8am(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, _at(2, 22, 59))
    watchdog = _watchdog(mariadb_client, NIGHT)

    watchdog.check(_at(2, 23, 30))
    watchdog.check(_at(3, 3, 0))
    watchdog.check(_at(3, 7, 59))
    assert len(responses.calls) == 0

    watchdog.check(_at(3, 8, 0))
    assert _titles() == ["hive-app: Hive data lost"]


@responses.activate
def test_an_outage_that_starts_and_heals_inside_quiet_hours_sends_nothing(
    mariadb_client: MariaDBClient,
) -> None:
    _poll_at(mariadb_client, _at(2, 22, 30))
    watchdog = _watchdog(mariadb_client, NIGHT)
    watchdog.check(_at(2, 23, 30))
    _poll_at(mariadb_client, _at(3, 7, 50))

    watchdog.check(_at(3, 8, 0))

    assert len(responses.calls) == 0


@responses.activate
def test_a_recovery_overnight_is_reported_at_8am_after_an_evening_alert(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, _at(2, 20, 59))
    watchdog = _watchdog(mariadb_client, NIGHT)
    watchdog.check(_at(2, 21, 30))
    _poll_at(mariadb_client, _at(3, 3, 0))

    watchdog.check(_at(3, 3, 1))
    assert _titles() == ["hive-app: Hive data lost"]

    _poll_at(mariadb_client, _at(3, 7, 58))
    watchdog.check(_at(3, 8, 0))
    assert _titles() == ["hive-app: Hive data lost", "hive-app: Hive data restored"]
    # Measured to when data returned (03:00), not to the 08:00 check.
    body = responses.calls[1].request.body
    assert body is not None
    assert "after 6 hours 1 minute." in body.decode("utf-8")


@responses.activate
def test_a_quiet_window_that_does_not_span_midnight_is_honoured(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, _at(2, 12, 0))
    afternoon = QuietHours(start=time(13, 0), end=time(15, 0))
    watchdog = _watchdog(mariadb_client, afternoon)

    watchdog.check(_at(2, 14, 0))
    assert len(responses.calls) == 0

    watchdog.check(_at(2, 15, 0))
    assert len(responses.calls) == 1


@responses.activate
def test_quiet_hours_are_read_in_the_configured_local_timezone(
    mariadb_client: MariaDBClient,
) -> None:
    set_local_timezone("America/New_York")
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, _at(2, 12, 0))
    watchdog = _watchdog(mariadb_client, NIGHT)

    # 04:00 UTC is 23:00 the evening before in New York: inside the window.
    watchdog.check(_at(3, 4, 0))
    assert len(responses.calls) == 0

    # 13:00 UTC is 08:00 in New York: the window has ended.
    watchdog.check(_at(3, 13, 0))
    assert len(responses.calls) == 1


@responses.activate
def test_a_poll_failure_older_than_the_last_good_row_is_not_given_as_the_cause(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, NOW - timedelta(minutes=31))
    _failed_poll(mariadb_client, NOW - timedelta(hours=2), "an old, healed failure")

    _watchdog(mariadb_client).check(NOW)

    body = responses.calls[0].request.body
    assert body is not None
    assert "Latest poll failure" not in body.decode("utf-8")


@responses.activate
def test_only_the_first_line_of_the_poll_failure_reaches_the_topic(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, NOW - timedelta(minutes=31))
    _failed_poll(
        mariadb_client,
        NOW - timedelta(minutes=5),
        "(builtins.TypeError) not JSON serializable\n[SQL: INSERT INTO t VALUES (1)]",
    )

    _watchdog(mariadb_client).check(NOW)

    body = responses.calls[0].request.body
    assert body is not None
    text = body.decode("utf-8")
    assert "(builtins.TypeError) not JSON serializable" in text
    assert "SQL" not in text


@responses.activate
def test_an_unreadable_failure_reason_does_not_suppress_the_alert(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, NOW - timedelta(minutes=31))

    def _unreadable(*_: object) -> str:
        raise RuntimeError("job_run unavailable")

    monkeypatch.setattr(mariadb_client, "latest_failed_job_error", _unreadable)

    _watchdog(mariadb_client).check(NOW)

    assert _titles() == ["hive-app: Hive data lost"]


@responses.activate
def test_a_failed_restored_notice_is_retried_on_the_next_check(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    responses.add(responses.POST, TOPIC_URL, status=500)
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, NOW - timedelta(minutes=31))
    watchdog = _watchdog(mariadb_client)
    watchdog.check(NOW)
    _poll_at(mariadb_client, NOW + timedelta(minutes=5))

    watchdog.check(NOW + timedelta(minutes=6))
    watchdog.check(NOW + timedelta(minutes=7))
    watchdog.check(NOW + timedelta(minutes=8))

    assert _titles() == [
        "hive-app: Hive data lost",
        "hive-app: Hive data restored",
        "hive-app: Hive data restored",
    ]

from datetime import UTC, datetime, timedelta

import responses

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


def _watchdog(mariadb_client: MariaDBClient) -> HiveDataWatchdog:
    return HiveDataWatchdog(mariadb_client, NtfyDataNotifier(TOPIC_URL))


@responses.activate
def test_data_missing_for_31_minutes_sends_a_high_priority_lost_alert_with_the_cause(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    _poll_at(mariadb_client, NOW - timedelta(minutes=31))
    mariadb_client.record_job_run(
        "heating_refresh", "failure", error="'NoneType' object has no attribute 'get'"
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
    assert "after 2 hours 2 minutes." in request.body.decode("utf-8")


@responses.activate
def test_no_restored_notice_when_no_lost_alert_was_delivered(
    mariadb_client: MariaDBClient,
) -> None:
    _poll_at(mariadb_client, NOW - timedelta(minutes=5))

    _watchdog(mariadb_client).check(NOW)

    assert len(responses.calls) == 0

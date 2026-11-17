import logging
from datetime import UTC, datetime, time, timedelta
from unittest.mock import Mock

import pytest
import responses
from schedule import Scheduler

from hive_app.common.config import (
    NtfySettings,
    PiholeSettings,
    PresenceSettings,
    QuietHours,
)
from hive_app.data.heating import HeatingRetriever
from hive_app.data.model import HeatingStatus
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.data.pihole import PiholeApiSource
from hive_app.data.presence import PresenceRetriever
from hive_app.data.watchdog import HiveDataWatchdog
from hive_app.main import (
    build_hive_watchdog,
    build_hive_watchdog_job,
    build_presence_job,
    register_heating_refresh_job,
    register_hive_watchdog_job,
    register_presence_job,
    run_pending_safely,
)

TOPIC_URL = "https://ntfy.sh/home-monitoring-hive-auth-ntfy-test"
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _status_at(polled_at: datetime) -> HeatingStatus:
    return HeatingStatus(
        polled_at=polled_at,
        current_temp=19.5,
        target_temp=21.0,
        mode="SCHEDULE",
        state="ON",
        boost_active=False,
        boost_ends_at=None,
        schedule={},
    )


def test_heating_refresh_job_runs_on_a_120_second_interval(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()

    job = register_heating_refresh_job(
        scheduler, Mock(spec=HeatingRetriever), mariadb_client
    )

    assert job.interval == 120
    assert job.unit == "seconds"


def test_a_successful_heating_refresh_is_recorded_as_a_successful_job_run(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()
    heating = Mock(spec=HeatingRetriever)

    job = register_heating_refresh_job(scheduler, heating, mariadb_client)
    job.run().join()

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()

    assert len(runs) == 1
    assert runs[0].job_name == "heating_refresh"
    assert runs[0].status == "success"
    assert runs[0].error_message is None


def test_a_persistently_failing_heating_refresh_retries_with_exponential_backoff(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep_delays: list[int] = []
    monkeypatch.setattr("hive_app.common.decorator.time.sleep", sleep_delays.append)
    scheduler = Scheduler()
    heating = Mock(spec=HeatingRetriever)
    heating.refresh.side_effect = RuntimeError("Hive backend unreachable")

    job = register_heating_refresh_job(scheduler, heating, mariadb_client)
    job.run().join()

    assert sleep_delays == [60, 120, 240, 480]
    assert heating.refresh.call_count == 5

    with mariadb_client.session_read_scope() as session:
        runs = session.query(model.job_run).all()

    assert len(runs) == 5
    assert all(run.status == "failure" for run in runs)
    assert all(run.error_message == "Hive backend unreachable" for run in runs)


def test_hive_watchdog_job_runs_every_60_seconds_only_when_ntfy_is_configured(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()

    assert build_hive_watchdog_job(scheduler, mariadb_client, None) is None
    job = build_hive_watchdog_job(
        scheduler, mariadb_client, NtfySettings(topic_url="https://ntfy.sh/some-topic")
    )

    assert job is not None
    assert job.interval == 60
    assert job.unit == "seconds"


def test_without_ntfy_a_warning_is_logged_and_no_watchdog_job_is_registered(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    scheduler = Scheduler()

    with caplog.at_level(logging.WARNING):
        job = build_hive_watchdog_job(scheduler, mariadb_client, None)

    assert job is None
    assert scheduler.get_jobs() == []
    assert "will not send a live alert if Hive data stops arriving" in caplog.text


def test_the_watchdog_job_swallows_a_failing_check() -> None:
    scheduler = Scheduler()
    watchdog = Mock(spec=HiveDataWatchdog)
    watchdog.check.side_effect = RuntimeError("database down")

    job = register_hive_watchdog_job(scheduler, watchdog)
    job.run()

    watchdog.check.assert_called_once()


def test_run_pending_safely_does_not_propagate_a_scheduled_job_failure() -> None:
    scheduler = Mock(spec=Scheduler)
    scheduler.run_pending.side_effect = RuntimeError("boom")

    run_pending_safely(scheduler)


@responses.activate
def test_the_configured_quiet_hours_reach_the_watchdog(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(responses.POST, TOPIC_URL, status=200)
    mariadb_client.write_heating_status(_status_at(NOW - timedelta(hours=3)))
    ntfy = NtfySettings(
        topic_url=TOPIC_URL,
        quiet_hours=QuietHours(start=time(11, 0), end=time(13, 0)),
    )

    watchdog = build_hive_watchdog(mariadb_client, ntfy)

    assert watchdog is not None
    watchdog.check(NOW)
    assert len(responses.calls) == 0
    watchdog.check(NOW + timedelta(hours=1))
    assert len(responses.calls) == 1


def test_without_ntfy_there_is_no_watchdog(mariadb_client: MariaDBClient) -> None:
    assert build_hive_watchdog(mariadb_client, None) is None


def _presence_settings() -> PresenceSettings:
    return PresenceSettings(
        pihole=PiholeSettings(url="http://pihole.example.lan", password="secret")
    )


def test_the_presence_job_runs_every_60_seconds_when_a_presence_section_is_configured(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()

    job = build_presence_job(
        scheduler, mariadb_client, _presence_settings(), Mock(spec=PiholeApiSource)
    )

    assert job is not None
    assert job.interval == 60
    assert job.unit == "seconds"


def test_without_a_presence_section_no_presence_job_is_scheduled(
    mariadb_client: MariaDBClient,
) -> None:
    scheduler = Scheduler()

    assert build_presence_job(scheduler, mariadb_client, None, None) is None
    assert scheduler.get_jobs() == []


def test_the_presence_job_swallows_a_failing_refresh() -> None:
    scheduler = Scheduler()
    presence = Mock(spec=PresenceRetriever)
    presence.refresh.side_effect = RuntimeError("database down")

    job = register_presence_job(scheduler, presence)
    job.run()

    presence.refresh.assert_called_once()

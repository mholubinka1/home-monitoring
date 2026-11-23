import logging
from datetime import UTC, datetime, time, timedelta
from unittest.mock import Mock

import pytest
import responses
from schedule import Scheduler

from common.local_day import set_local_timezone
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
from hive_app.data.presence_watchdog import PresenceWatchdog
from hive_app.data.watchdog import HiveDataWatchdog
from hive_app.main import (
    build_heating_watchdog_job,
    build_hive_watchdog,
    build_hive_watchdog_job,
    build_presence_job,
    build_presence_watchdog_job,
    build_presence_watchdog_jobs,
    register_heating_refresh_job,
    register_hive_watchdog_job,
    register_presence_job,
    register_presence_night_check_job,
    register_presence_watchdog_job,
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


def test_presence_watchdog_job_runs_every_60_seconds_only_when_ntfy_is_configured(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    scheduler = Scheduler()

    with caplog.at_level(logging.WARNING):
        assert build_presence_watchdog_job(scheduler, mariadb_client, None) is None
    assert scheduler.get_jobs() == []
    assert "the presence signal is lost" in caplog.text

    job = build_presence_watchdog_job(
        scheduler, mariadb_client, NtfySettings(topic_url="https://ntfy.sh/some-topic")
    )
    assert job is not None
    assert job.interval == 60
    assert job.unit == "seconds"


def test_the_night_check_runs_once_a_day_only_just_after_0305_local_time(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[datetime] = []
    monkeypatch.setattr(
        "hive_app.main.warn_if_house_empty", lambda _db, now: calls.append(now)
    )
    clock = [datetime(2026, 11, 17, 2, 59, tzinfo=UTC)]
    monkeypatch.setattr("hive_app.main.datetime", Mock(now=lambda _tz: clock[0]))
    job = register_presence_night_check_job(Scheduler(), mariadb_client)

    for moment in (
        datetime(2026, 11, 17, 2, 59, tzinfo=UTC),
        datetime(2026, 11, 17, 3, 5, tzinfo=UTC),
        datetime(2026, 11, 17, 3, 6, tzinfo=UTC),
        datetime(2026, 11, 17, 10, 0, tzinfo=UTC),
    ):
        clock[0] = moment
        job.run()

    assert calls == [datetime(2026, 11, 17, 3, 5, tzinfo=UTC)]


def test_the_night_check_is_read_in_the_configured_local_timezone(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # In June London is an hour ahead of UTC, so 03:05 local is 02:05 UTC.
    set_local_timezone("Europe/London")
    calls: list[datetime] = []
    monkeypatch.setattr(
        "hive_app.main.warn_if_house_empty", lambda _db, now: calls.append(now)
    )
    clock = [datetime(2026, 6, 17, 3, 5, tzinfo=UTC)]
    monkeypatch.setattr("hive_app.main.datetime", Mock(now=lambda _tz: clock[0]))
    job = register_presence_night_check_job(Scheduler(), mariadb_client)

    job.run()
    clock[0] = datetime(2026, 6, 17, 2, 5, tzinfo=UTC)
    job.run()

    assert calls == [datetime(2026, 6, 17, 2, 5, tzinfo=UTC)]


def test_the_presence_watchdog_job_swallows_a_failing_check() -> None:
    watchdog = Mock(spec=PresenceWatchdog)
    watchdog.check.side_effect = RuntimeError("database down")

    job = register_presence_watchdog_job(Scheduler(), watchdog)
    job.run()

    watchdog.check.assert_called_once()


def test_a_failed_night_check_is_retried_on_the_next_tick(
    mariadb_client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[datetime] = []

    def flaky(_db: MariaDBClient, now: datetime) -> None:
        attempts.append(now)
        if len(attempts) == 1:
            raise RuntimeError("database restarting")

    monkeypatch.setattr("hive_app.main.warn_if_house_empty", flaky)
    clock = [datetime(2026, 11, 17, 3, 5, tzinfo=UTC)]
    monkeypatch.setattr("hive_app.main.datetime", Mock(now=lambda _tz: clock[0]))
    job = register_presence_night_check_job(Scheduler(), mariadb_client)

    job.run()
    clock[0] = datetime(2026, 11, 17, 3, 6, tzinfo=UTC)
    job.run()
    clock[0] = datetime(2026, 11, 17, 3, 7, tzinfo=UTC)
    job.run()

    assert len(attempts) == 2


def test_presence_watchdog_jobs_exist_only_when_presence_is_configured(
    mariadb_client: MariaDBClient,
) -> None:
    ntfy = NtfySettings(topic_url=TOPIC_URL)
    presence = PresenceSettings(
        pihole=PiholeSettings(url="http://pihole.example.lan", password="secret")
    )

    assert not build_presence_watchdog_jobs(Scheduler(), mariadb_client, None, ntfy)
    assert (
        len(build_presence_watchdog_jobs(Scheduler(), mariadb_client, presence, ntfy))
        == 2
    )
    # Without ntfy only the night check remains.
    assert (
        len(build_presence_watchdog_jobs(Scheduler(), mariadb_client, presence, None))
        == 1
    )


def test_heating_watchdog_job_runs_every_60_seconds_only_with_ntfy_and_comfort_on(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a scheduler, and ntfy configured or not
    scheduler = Scheduler()
    ntfy = NtfySettings(topic_url="https://ntfy.sh/some-topic")

    # When the heating watchdog job is built without ntfy, or with the Comfort
    # Schedule not on, then nothing is scheduled
    assert build_heating_watchdog_job(scheduler, mariadb_client, None, "on") is None
    assert (
        build_heating_watchdog_job(scheduler, mariadb_client, ntfy, "dry_run") is None
    )
    assert scheduler.get_jobs() == []

    # When it is built with ntfy and the Comfort Schedule on, then it runs every
    # 60 seconds
    job = build_heating_watchdog_job(scheduler, mariadb_client, ntfy, "on")
    assert job is not None
    assert (job.interval, job.unit) == (60, "seconds")

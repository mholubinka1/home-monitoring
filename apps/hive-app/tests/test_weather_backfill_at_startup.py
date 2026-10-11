import json
import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
import responses
from weather_hourly_payloads import hourly_payload

from hive_app.data.model import ResolvedLocation, WeatherObservation
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.main import run_weather_backfill_at_startup

ARCHIVE_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
NOW = datetime(2026, 10, 11, 9, 30, tzinfo=UTC)
FIRST_HOUR = datetime(2024, 7, 24, 0, tzinfo=UTC)
HOME_KEY = "51.40,-0.05"


def _store(
    mariadb: MariaDBClient, observed_at: datetime, source: str, location: str
) -> None:
    mariadb.write_weather_observations(
        [
            WeatherObservation(
                source=source,
                location=location,
                observed_at=observed_at,
                temp=10.0,
                humidity=None,
                pressure=None,
                wind_speed=None,
                precipitation=None,
            )
        ]
    )


def _job_runs(mariadb: MariaDBClient) -> list[model.job_run]:
    with mariadb.session_read_scope() as session:
        return session.query(model.job_run).all()


@responses.activate
def test_with_no_stored_weather_startup_backfills_and_records_success(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    responses.add(
        responses.GET, ARCHIVE_ENDPOINT, json=hourly_payload(["2024-07-24T00:00"])
    )

    worker = run_weather_backfill_at_startup(mariadb_client, clock=lambda: NOW)
    assert worker is not None
    worker.join()

    assert [(run.job_name, run.status) for run in _job_runs(mariadb_client)] == [
        ("weather_history_backfill", "success")
    ]
    # Newest chunk first, so the range's start is requested last.
    assert responses.calls[-1].request.params["start_date"] == "2024-07-24"


@pytest.mark.parametrize("source", ["open-meteo", "open-meteo-archive"])
@responses.activate
def test_stored_weather_reaching_the_first_hour_skips_the_backfill(
    mariadb_client: MariaDBClient, source: str
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    # A recent hour from the other source too: the oldest hour across both counts.
    other = "open-meteo-archive" if source == "open-meteo" else "open-meteo"
    _store(mariadb_client, datetime(2026, 10, 6, 12, tzinfo=UTC), other, HOME_KEY)
    _store(mariadb_client, FIRST_HOUR, source, HOME_KEY)

    assert mariadb_client.oldest_weather_observation(HOME_KEY) == FIRST_HOUR
    assert run_weather_backfill_at_startup(mariadb_client, clock=lambda: NOW) is None

    assert len(responses.calls) == 0
    assert _job_runs(mariadb_client) == []


@responses.activate
def test_stored_weather_starting_later_still_backfills(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    _store(mariadb_client, datetime(2024, 7, 24, 1, tzinfo=UTC), "open-meteo", HOME_KEY)
    responses.add(
        responses.GET, ARCHIVE_ENDPOINT, json=hourly_payload(["2024-07-24T00:00"])
    )

    worker = run_weather_backfill_at_startup(mariadb_client, clock=lambda: NOW)
    assert worker is not None
    worker.join()

    assert [run.status for run in _job_runs(mariadb_client)] == ["success"]


@responses.activate
def test_stored_weather_under_another_location_does_not_count(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    _store(mariadb_client, FIRST_HOUR, "open-meteo-archive", "52.00,-1.00")
    responses.add(
        responses.GET, ARCHIVE_ENDPOINT, json=hourly_payload(["2024-07-24T00:00"])
    )

    worker = run_weather_backfill_at_startup(mariadb_client, clock=lambda: NOW)
    assert worker is not None
    worker.join()

    # The backfill ran for the cached location, not the other one.
    assert mariadb_client.oldest_weather_observation(HOME_KEY) == FIRST_HOUR
    assert [run.status for run in _job_runs(mariadb_client)] == ["success"]


@responses.activate
def test_without_a_cached_location_startup_does_not_backfill(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="hive-monitor"):
        assert (
            run_weather_backfill_at_startup(mariadb_client, clock=lambda: NOW) is None
        )

    assert len(responses.calls) == 0
    assert "next restart will backfill" in caplog.text
    assert _job_runs(mariadb_client) == []


@responses.activate
def test_a_failing_backfill_is_retried_recorded_and_never_raised(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    responses.add(responses.GET, ARCHIVE_ENDPOINT, status=500)

    with (
        caplog.at_level(logging.DEBUG, logger="hive-monitor"),
        patch("hive_app.common.decorator.time.sleep"),
        patch("hive_app.weather_backfill.time.sleep"),
    ):
        worker = run_weather_backfill_at_startup(mariadb_client, clock=lambda: NOW)
        assert worker is not None
        worker.join()

    runs = _job_runs(mariadb_client)
    assert len(runs) == 5
    assert {(run.job_name, run.status) for run in runs} == {
        ("weather_history_backfill", "failure")
    }
    # The archive's 500 is a requests error whose text is the URL with the
    # coordinates; neither the recorded errors nor the logged retries carry it.
    assert all("51.4" not in (run.error_message or "") for run in runs)
    assert "51.4" not in caplog.text


@responses.activate
def test_a_successful_backfill_logs_hours_stored_and_complete_days(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    responses.add(
        responses.GET, ARCHIVE_ENDPOINT, json=hourly_payload(["2024-07-24T00:00"])
    )

    with caplog.at_level(logging.INFO, logger="hive-monitor"):
        worker = run_weather_backfill_at_startup(mariadb_client, clock=lambda: NOW)
        assert worker is not None
        worker.join()

    # 2024-07-24 to 2026-10-10 is 809 local days; a few stored hours complete none.
    assert "stored 3 hours" in caplog.text
    assert "complete days: 0 of 809" in caplog.text
    assert "51.4" not in caplog.text


# The write fails inside a chunk, which the backfill wraps; the location read
# fails before any chunk, so its error reaches the job unwrapped.
@pytest.mark.parametrize(
    "failing_call", ["write_weather_observations", "read_weather_location"]
)
@responses.activate
def test_a_raw_error_never_reaches_job_run_or_the_logs(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture, failing_call: str
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    responses.add(
        responses.GET, ARCHIVE_ENDPOINT, json=hourly_payload(["2024-07-24T00:00"])
    )
    raw_error = ValueError("lat=51.40 lon=-0.05")
    # The startup check reads the location before the job starts, so only the
    # backfill's own reads, one per attempt, fail.
    failures: Any = (
        raw_error
        if failing_call == "write_weather_observations"
        else [mariadb_client.read_weather_location()] + [raw_error] * 5
    )

    # Fail at the database boundary.
    with (
        caplog.at_level(logging.DEBUG, logger="hive-monitor"),
        patch.object(MariaDBClient, failing_call, side_effect=failures),
        patch("hive_app.common.decorator.time.sleep"),
    ):
        worker = run_weather_backfill_at_startup(mariadb_client, clock=lambda: NOW)
        assert worker is not None
        worker.join()

    runs = _job_runs(mariadb_client)
    assert runs
    assert {run.status for run in runs} == {"failure"}
    assert all("51.40" not in (run.error_message or "") for run in runs)
    assert "51.40" not in caplog.text


@responses.activate
def test_a_failing_completeness_report_does_not_fail_or_repeat_the_stored_backfill(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    responses.add(
        responses.GET, ARCHIVE_ENDPOINT, json=hourly_payload(["2024-07-24T00:00"])
    )

    with (
        caplog.at_level(logging.DEBUG, logger="hive-monitor"),
        patch.object(
            MariaDBClient,
            "read_weather_observation_hours",
            side_effect=ValueError("lat=51.40 lon=-0.05"),
        ),
        patch("hive_app.common.decorator.time.sleep"),
    ):
        worker = run_weather_backfill_at_startup(mariadb_client, clock=lambda: NOW)
        assert worker is not None
        worker.join()

    assert [run.status for run in _job_runs(mariadb_client)] == ["success"]
    # 2024-07-24 to 2026-10-11 is three chunks, requested once each.
    assert len(responses.calls) == 3
    assert "completeness report unavailable (ValueError)" in caplog.text
    assert "stored 3 hours" in caplog.text
    assert "51.4" not in caplog.text


# The three chunks of the 2024-07-24 to 2026-10-11 range: oldest, middle, newest.
# A middle or newest failure is what an oldest-first order got wrong: the oldest
# chunk was already stored, so the gate skipped every later restart.
@pytest.mark.parametrize("failing_start", ["2024-07-24", "2024-10-12", "2025-10-12"])
@responses.activate
def test_a_persistently_failing_chunk_leaves_the_gap_to_be_retried_at_the_next_start(
    mariadb_client: MariaDBClient, failing_start: str
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))

    # Any: responses adds `params` to the PreparedRequest it hands the callback.
    def fail_the_chunk_starting_on(request: Any) -> tuple[int, dict[str, str], str]:
        start = request.params["start_date"]
        if start == failing_start:
            return 500, {}, ""
        # The chunk's own first hour, so a stored chunk moves the oldest stored
        # hour exactly as real data would.
        return 200, {}, json.dumps(hourly_payload([f"{start}T00:00"]))

    responses.add_callback(
        responses.GET, ARCHIVE_ENDPOINT, callback=fail_the_chunk_starting_on
    )

    with (
        patch("hive_app.common.decorator.time.sleep"),
        patch("hive_app.weather_backfill.time.sleep"),
    ):
        worker = run_weather_backfill_at_startup(mariadb_client, clock=lambda: NOW)
        assert worker is not None
        worker.join()

        # Newer chunks are stored, but the history does not reach back to the
        # start, so the next start backfills again.
        calls_before = len(responses.calls)
        again = run_weather_backfill_at_startup(mariadb_client, clock=lambda: NOW)
        assert again is not None
        again.join()

    assert len(responses.calls) > calls_before


@pytest.mark.parametrize(
    "failing_read", ["read_weather_location", "oldest_weather_observation"]
)
@responses.activate
def test_a_database_error_in_the_startup_check_is_logged_and_startup_continues(
    mariadb_client: MariaDBClient, caplog: pytest.LogCaptureFixture, failing_read: str
) -> None:
    # A cached location, so the check reaches its second read too.
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    with (
        caplog.at_level(logging.INFO, logger="hive-monitor"),
        patch.object(
            MariaDBClient,
            failing_read,
            side_effect=ConnectionError("SELECT ... lat=51.40"),
        ),
    ):
        assert (
            run_weather_backfill_at_startup(mariadb_client, clock=lambda: NOW) is None
        )

    assert len(responses.calls) == 0
    assert "ConnectionError" in caplog.text
    assert "51.40" not in caplog.text

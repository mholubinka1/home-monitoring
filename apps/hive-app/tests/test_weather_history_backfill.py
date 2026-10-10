# ruff: noqa: DTZ001
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import responses
from weather_hourly_payloads import hourly_payload

from hive_app.data.model import ResolvedLocation, WeatherObservation
from hive_app.data.mysql import model
from hive_app.data.mysql.client import MariaDBClient
from hive_app.weather_backfill import (
    LOCAL_TIMEZONE,
    WeatherHistoryBackfill,
    completeness_report,
    main,
)

ARCHIVE_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
NOW = datetime(2026, 10, 10, 21, 53, tzinfo=UTC)


def _no_sleep(_seconds: float) -> None:
    pass


@responses.activate
def test_a_run_stores_each_archive_hour_labelled_as_history(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    responses.add(
        responses.GET,
        ARCHIVE_ENDPOINT,
        json=hourly_payload(
            ["2024-07-24T00:00", "2024-07-24T01:00", "2024-07-24T02:00"]
        ),
        status=200,
    )

    WeatherHistoryBackfill(mariadb_client, clock=lambda: NOW, sleep=_no_sleep).run(
        date(2024, 7, 24), date(2024, 7, 24)
    )

    with mariadb_client.session_read_scope() as session:
        rows = session.query(model.weather_observation).all()
    assert sorted(
        (row.source, row.location, row.observed_at, row.temp) for row in rows
    ) == [
        ("open-meteo-archive", "51.40,-0.05", datetime(2024, 7, 24, 0), 15.0),
        ("open-meteo-archive", "51.40,-0.05", datetime(2024, 7, 24, 1), 16.0),
        ("open-meteo-archive", "51.40,-0.05", datetime(2024, 7, 24, 2), 17.0),
    ]


def _stored(mariadb_client: MariaDBClient) -> list[tuple]:
    with mariadb_client.session_read_scope() as session:
        rows = session.query(model.weather_observation).all()
    return sorted((row.source, row.location, row.observed_at, row.temp) for row in rows)


@responses.activate
def test_a_second_run_over_the_same_range_replaces_values_without_adding_rows(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    hours = ["2024-07-24T00:00", "2024-07-24T01:00"]
    responses.add(responses.GET, ARCHIVE_ENDPOINT, json=hourly_payload(hours))
    backfill = WeatherHistoryBackfill(
        mariadb_client, clock=lambda: NOW, sleep=_no_sleep
    )
    backfill.run(date(2024, 7, 24), date(2024, 7, 24))
    responses.replace(
        responses.GET,
        ARCHIVE_ENDPOINT,
        json=hourly_payload(hours, temperature_2m=[5.0, 6.0]),
    )

    backfill.run(date(2024, 7, 24), date(2024, 7, 24))

    assert [(row[2], row[3]) for row in _stored(mariadb_client)] == [
        (datetime(2024, 7, 24, 0), 5.0),
        (datetime(2024, 7, 24, 1), 6.0),
    ]


@responses.activate
def test_live_rows_for_the_same_hours_are_untouched_by_the_backfill(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    mariadb_client.write_weather_observations(
        [
            WeatherObservation(
                source="open-meteo",
                location="51.40,-0.05",
                observed_at=datetime(2024, 7, 24, 0, tzinfo=UTC),
                temp=99.0,
                humidity=None,
                pressure=None,
                wind_speed=None,
                precipitation=None,
            )
        ]
    )
    responses.add(
        responses.GET, ARCHIVE_ENDPOINT, json=hourly_payload(["2024-07-24T00:00"])
    )

    WeatherHistoryBackfill(mariadb_client, clock=lambda: NOW, sleep=_no_sleep).run(
        date(2024, 7, 24), date(2024, 7, 24)
    )

    assert _stored(mariadb_client) == [
        ("open-meteo", "51.40,-0.05", datetime(2024, 7, 24, 0), 99.0),
        ("open-meteo-archive", "51.40,-0.05", datetime(2024, 7, 24, 0), 15.0),
    ]


@responses.activate
def test_a_multi_year_range_is_requested_in_consecutive_year_long_chunks(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    responses.add(responses.GET, ARCHIVE_ENDPOINT, json=hourly_payload([]))

    results = WeatherHistoryBackfill(
        mariadb_client, clock=lambda: NOW, sleep=_no_sleep
    ).run(date(2023, 1, 1), date(2025, 6, 30))

    requested = [
        (call.request.params["start_date"], call.request.params["end_date"])
        for call in responses.calls
    ]
    assert requested == [
        ("2023-01-01", "2023-12-31"),
        ("2024-01-01", "2024-12-30"),
        ("2024-12-31", "2025-06-30"),
    ]
    assert [(r.start, r.end) for r in results] == [
        (date(2023, 1, 1), date(2023, 12, 31)),
        (date(2024, 1, 1), date(2024, 12, 30)),
        (date(2024, 12, 31), date(2025, 6, 30)),
    ]


@responses.activate
def test_a_failed_chunk_keeps_earlier_chunks_and_names_the_date_to_repeat_from(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    responses.add(
        responses.GET,
        ARCHIVE_ENDPOINT,
        json=hourly_payload(["2023-01-01T00:00"]),
    )
    responses.add(responses.GET, ARCHIVE_ENDPOINT, status=500)
    sleeps: list[float] = []

    with pytest.raises(RuntimeError, match="Repeat from 2024-01-01") as failure:
        WeatherHistoryBackfill(
            mariadb_client, clock=lambda: NOW, sleep=sleeps.append
        ).run(date(2023, 1, 1), date(2025, 6, 30))

    assert "HTTP 500" in str(failure.value)
    assert "51.4" not in str(failure.value)

    assert [row[2] for row in _stored(mariadb_client)] == [datetime(2023, 1, 1, 0)]
    assert len(responses.calls) == 1 + 3
    assert len(sleeps) == 2


@responses.activate
def test_a_chunk_that_succeeds_on_a_retry_is_stored(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    responses.add(responses.GET, ARCHIVE_ENDPOINT, status=500)
    responses.add(
        responses.GET, ARCHIVE_ENDPOINT, json=hourly_payload(["2024-07-24T00:00"])
    )

    results = WeatherHistoryBackfill(
        mariadb_client, clock=lambda: NOW, sleep=_no_sleep
    ).run(date(2024, 7, 24), date(2024, 7, 24))

    assert [r.rows_written for r in results] == [1]


@responses.activate
def test_without_a_cached_location_the_run_stops_and_makes_no_request(
    mariadb_client: MariaDBClient,
) -> None:
    with pytest.raises(RuntimeError, match="let hive-app resolve a location"):
        WeatherHistoryBackfill(mariadb_client, clock=lambda: NOW, sleep=_no_sleep).run(
            date(2024, 7, 24), date(2024, 7, 24)
        )

    assert len(responses.calls) == 0


@responses.activate
def test_a_changed_location_stores_under_its_own_key_and_leaves_earlier_rows(
    mariadb_client: MariaDBClient,
) -> None:
    responses.add(
        responses.GET, ARCHIVE_ENDPOINT, json=hourly_payload(["2024-07-24T00:00"])
    )
    backfill = WeatherHistoryBackfill(
        mariadb_client, clock=lambda: NOW, sleep=_no_sleep
    )
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    backfill.run(date(2024, 7, 24), date(2024, 7, 24))
    mariadb_client.write_weather_location(ResolvedLocation(53.5, -2.25, "postcode"))

    backfill.run(date(2024, 7, 24), date(2024, 7, 24))

    assert [row[1] for row in _stored(mariadb_client)] == [
        "51.40,-0.05",
        "53.50,-2.25",
    ]


@responses.activate
def test_hours_after_the_latest_completed_hour_are_not_stored(
    mariadb_client: MariaDBClient,
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    responses.add(
        responses.GET,
        ARCHIVE_ENDPOINT,
        json=hourly_payload(
            ["2026-10-10T20:00", "2026-10-10T21:00", "2026-10-10T22:00"]
        ),
    )

    WeatherHistoryBackfill(mariadb_client, clock=lambda: NOW, sleep=_no_sleep).run(
        date(2026, 10, 10), date(2026, 10, 10)
    )

    assert [row[2] for row in _stored(mariadb_client)] == [
        datetime(2026, 10, 10, 20),
        datetime(2026, 10, 10, 21),
    ]


@pytest.mark.usefixtures("mariadb_client")
def test_the_command_without_a_cached_location_fails_telling_the_operator_why(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        "hive:\n"
        "  username: user@example.com\n"
        "  password: hunter2\n"
        f"  auth_state_path: {tmp_path / 'state.json'}\n"
        "mariadb:\n"
        "  host: localhost\n"
        "  port: 3306\n"
        "  database: main\n"
        "  username: test\n"
        "  password: test\n",
        encoding="utf-8",
    )

    exit_code = main(["--config-file", str(config_file)])

    assert exit_code == 1
    assert "let hive-app resolve a location" in capsys.readouterr().err


def _hour_rows(source: str, location: str, first: datetime, count: int) -> list:
    return [
        WeatherObservation(
            source=source,
            location=location,
            observed_at=first + timedelta(hours=i),
            temp=10.0,
            humidity=None,
            pressure=None,
            wind_speed=None,
            precipitation=None,
        )
        for i in range(count)
    ]


def test_a_spring_forward_day_with_all_23_hours_is_reported_complete(
    mariadb_client: MariaDBClient,
) -> None:
    # 2025-03-30 in London runs from 00:00 GMT to 00:00 BST: 00:00Z to 23:00Z.
    mariadb_client.write_weather_observations(
        _hour_rows(
            "open-meteo-archive", "51.40,-0.05", datetime(2025, 3, 30, tzinfo=UTC), 23
        )
    )

    report = completeness_report(
        mariadb_client, "51.40,-0.05", date(2025, 3, 30), date(2025, 3, 30)
    )

    assert report.complete_days == 1
    assert not report.incomplete_days


def test_an_autumn_day_with_25_hours_and_a_normal_day_with_24_are_complete(
    mariadb_client: MariaDBClient,
) -> None:
    # 2025-10-26 runs from 23:00Z on the 25th to 00:00Z on the 27th (BST to GMT).
    mariadb_client.write_weather_observations(
        _hour_rows(
            "open-meteo-archive",
            "51.40,-0.05",
            datetime(2025, 10, 25, 23, tzinfo=UTC),
            25 + 24,
        )
    )

    report = completeness_report(
        mariadb_client, "51.40,-0.05", date(2025, 10, 26), date(2025, 10, 27)
    )

    assert report.complete_days == 2
    assert not report.incomplete_days


def test_a_day_missing_an_hour_is_incomplete_and_a_day_with_no_rows_misses_them_all(
    mariadb_client: MariaDBClient,
) -> None:
    # 2025-06-10 is BST: its local midnight is 23:00Z on the 9th. Dropping the
    # 12:00Z row leaves 2025-06-10 one hour short; the 23:00Z row on the 10th
    # is local 00:00 on the 11th, so the 11th is short by 23 of its 24.
    rows = _hour_rows(
        "open-meteo-archive", "51.40,-0.05", datetime(2025, 6, 10, tzinfo=UTC), 24
    )
    mariadb_client.write_weather_observations(rows[:12] + rows[13:])

    report = completeness_report(
        mariadb_client, "51.40,-0.05", date(2025, 6, 10), date(2025, 6, 12)
    )

    assert report.complete_days == 0
    assert report.incomplete_days == {
        date(2025, 6, 10): 2,
        date(2025, 6, 11): 23,
        date(2025, 6, 12): 24,
    }


def test_an_hour_held_by_either_source_counts_but_another_location_does_not(
    mariadb_client: MariaDBClient,
) -> None:
    first = datetime(2025, 1, 15, tzinfo=UTC)
    mariadb_client.write_weather_observations(
        _hour_rows("open-meteo-archive", "51.40,-0.05", first, 12)
        + _hour_rows("open-meteo", "51.40,-0.05", first + timedelta(hours=12), 11)
        + _hour_rows("open-meteo", "53.50,-2.25", first + timedelta(hours=23), 1)
    )

    report = completeness_report(
        mariadb_client, "51.40,-0.05", date(2025, 1, 15), date(2025, 1, 15)
    )

    assert report.incomplete_days == {date(2025, 1, 15): 1}


def test_the_report_counts_archive_rows_per_local_month(
    mariadb_client: MariaDBClient,
) -> None:
    # 23:00Z on 31 March is already April in London (BST); live rows are not counted.
    mariadb_client.write_weather_observations(
        _hour_rows(
            "open-meteo-archive",
            "51.40,-0.05",
            datetime(2025, 3, 31, 22, tzinfo=UTC),
            3,
        )
        + _hour_rows("open-meteo", "51.40,-0.05", datetime(2025, 3, 20, tzinfo=UTC), 5)
    )

    report = completeness_report(
        mariadb_client, "51.40,-0.05", date(2025, 3, 1), date(2025, 4, 2)
    )

    assert report.archive_rows_per_month == {"2025-03": 1, "2025-04": 2}


@responses.activate
def test_a_successful_run_prints_the_report_up_to_yesterday(
    mariadb_client: MariaDBClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mariadb_client.write_weather_location(ResolvedLocation(51.4, -0.05, "postcode"))
    yesterday = datetime.now(LOCAL_TIMEZONE).date() - timedelta(days=1)
    responses.add(
        responses.GET,
        ARCHIVE_ENDPOINT,
        json=hourly_payload([f"{yesterday}T00:00", f"{yesterday}T01:00"]),
    )
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        "hive:\n"
        "  username: user@example.com\n"
        "  password: hunter2\n"
        f"  auth_state_path: {tmp_path / 'state.json'}\n"
        "mariadb:\n"
        "  host: localhost\n"
        "  port: 3306\n"
        "  database: main\n"
        "  username: test\n"
        "  password: test\n",
        encoding="utf-8",
    )

    exit_code = main(["--config-file", str(config_file), "--start", str(yesterday)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert f"{yesterday:%Y-%m}: 2 archive hours" in out
    assert "complete days: 0 of 1" in out
    assert f"incomplete: {yesterday}," in out
    assert "51.4" not in out

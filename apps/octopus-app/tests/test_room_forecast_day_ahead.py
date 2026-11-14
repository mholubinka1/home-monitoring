"""The Day-Ahead Forecast: tomorrow's hours of each live forecast, rewritten by
every run until midnight and then frozen (ADR-0035).

London is on GMT in November, so local time is UTC."""

from datetime import UTC, datetime, timedelta
from typing import Any

from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.room_forecast_job import RoomForecastJob

from .room_forecast_fixtures import (  # noqa: F401  # pylint: disable=unused-import
    NOW,
    REGION,
    live_rows,
    seed_comfort,
    seed_plan_inputs,
    seed_reading,
    seed_schedule,
    seed_weather_forecast,
    thermal_model_client,
    weekly_schedule,
)


def day_ahead_rows(client: MariaDBClient, scenario: str) -> list[Any]:
    rows = model.room_forecast_hour
    with client.session_read_scope() as session:
        return (
            session.query(rows)
            .filter(rows.kind == "day_ahead", rows.scenario == scenario)
            .order_by(rows.hour_start)
            .all()
        )


def test_a_run_today_stores_day_ahead_rows_for_tomorrows_hours_only(
    client: MariaDBClient,
) -> None:
    # Given a run on Tuesday 17 November with a comfort temperature, so both
    # scenarios are forecast
    seed_plan_inputs(client)
    seed_comfort(client, NOW.date(), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then each scenario has day-ahead rows for Wednesday's 24 hours and none for
    # today, and they match the live rows of those hours
    for scenario in ("current", "plan"):
        day_ahead = day_ahead_rows(client, scenario)
        assert [row.hour_start for row in day_ahead] == [
            datetime(2026, 11, 18, hour) for hour in range(24)
        ]
        live_tomorrow = [
            row
            for row in live_rows(client, scenario)
            if row.hour_start.date() == datetime(2026, 11, 18).date()
        ]
        assert [row.room_temp for row in day_ahead] == [
            row.room_temp for row in live_tomorrow
        ]


def test_a_later_run_the_same_day_rewrites_tomorrows_day_ahead_rows(
    client: MariaDBClient,
) -> None:
    # Given a run at 14:10 on a reading of 18 C
    seed_plan_inputs(client, room=18.0)
    RoomForecastJob(client, REGION).refresh(NOW)

    # When a run an hour later reads a different room temperature
    later = NOW + timedelta(hours=1)
    seed_reading(client, later - timedelta(minutes=5), room=20.0)
    RoomForecastJob(client, REGION).refresh(later)

    # Then tomorrow's day-ahead rows are the second run's, still 24 of them
    day_ahead = day_ahead_rows(client, "current")
    assert len(day_ahead) == 24
    live_tomorrow = [
        row for row in live_rows(client, "current") if row.hour_start.day == 18
    ]
    assert [row.room_temp for row in day_ahead] == [
        row.room_temp for row in live_tomorrow
    ]
    assert {row.computed_at for row in day_ahead} == {later.replace(tzinfo=None)}


def test_once_midnight_passes_that_days_day_ahead_rows_stay_frozen(
    client: MariaDBClient,
) -> None:
    # Given a run on Tuesday 17 November at 23:30
    last_run = datetime(2026, 11, 17, 23, 30, tzinfo=UTC)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 23), last=datetime(2026, 11, 21, 0)
    )
    seed_reading(client, last_run - timedelta(minutes=5), room=18.0)
    RoomForecastJob(client, REGION).refresh(last_run)
    frozen = [
        (row.hour_start, row.room_temp, row.computed_at)
        for row in day_ahead_rows(client, "current")
    ]
    assert len(frozen) == 24

    # When a run after midnight reads a different room temperature
    after_midnight = datetime(2026, 11, 18, 0, 20, tzinfo=UTC)
    seed_reading(client, after_midnight - timedelta(minutes=5), room=21.0)
    RoomForecastJob(client, REGION).refresh(after_midnight)

    # Then Wednesday's day-ahead rows are as the 23:30 run wrote them, Thursday's
    # are written, and nothing is stored for Tuesday
    day_ahead = day_ahead_rows(client, "current")
    assert [
        (row.hour_start, row.room_temp, row.computed_at)
        for row in day_ahead
        if row.hour_start.day == 18
    ] == frozen
    assert [row.hour_start for row in day_ahead if row.hour_start.day == 19] == [
        datetime(2026, 11, 19, hour) for hour in range(24)
    ]
    assert not [row for row in day_ahead if row.hour_start.day == 17]


def test_a_skipped_run_leaves_the_day_ahead_rows_untouched(
    client: MariaDBClient,
) -> None:
    # Given day-ahead rows from a run
    seed_plan_inputs(client, room=18.0)
    RoomForecastJob(client, REGION).refresh(NOW)
    before = [
        (row.hour_start, row.room_temp, row.computed_at)
        for row in day_ahead_rows(client, "current")
    ]

    # When a later run finds the newest reading stale
    RoomForecastJob(client, REGION).refresh(NOW + timedelta(hours=1))

    # Then the day-ahead rows are as they were
    assert [
        (row.hour_start, row.room_temp, row.computed_at)
        for row in day_ahead_rows(client, "current")
    ] == before
    assert len(before) == 24

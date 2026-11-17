"""The Day-Ahead Forecast: tomorrow's rows of each live forecast, rewritten by
every run until midnight and then frozen (ADR-0035).

London is on GMT in November, so local time is UTC."""

from datetime import UTC, datetime, timedelta
from typing import Any

from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient, weather_forecast_hour_table
from octopus_app.data.room_forecast_job import RoomForecastJob

from .room_forecast_fixtures import (  # noqa: F401  # pylint: disable=unused-import
    NOW,
    PINNED_WINDOWS,
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

TEN_MINUTE_ROWS_A_DAY = 24 * 6


def every_ten_minutes(day: int) -> list[datetime]:
    """The 144 row starts of a day in November 2026."""
    return [
        datetime(2026, 11, day, hour, minute)
        for hour in range(24)
        for minute in range(0, 60, 10)
    ]


def day_ahead_rows(client: MariaDBClient, scenario: str) -> list[Any]:
    rows = model.room_forecast_interval
    with client.session_read_scope() as session:
        return (
            session.query(rows)
            .filter(rows.kind == "day_ahead", rows.scenario == scenario)
            .order_by(rows.starts_at)
            .all()
        )


def test_a_run_today_stores_day_ahead_rows_for_every_ten_minutes_of_tomorrow_only(
    client: MariaDBClient,
) -> None:
    # Given a run on Tuesday 17 November with a comfort temperature, so both
    # scenarios are forecast
    seed_plan_inputs(client)
    seed_comfort(client, NOW.date(), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then each scenario has day-ahead rows for every 10 minutes of Wednesday and
    # none for today, and they match the live rows of those minutes
    for scenario in ("current", "plan"):
        day_ahead = day_ahead_rows(client, scenario)
        assert [row.starts_at for row in day_ahead] == every_ten_minutes(18)
        live_tomorrow = [
            row
            for row in live_rows(client, scenario)
            if row.starts_at.date() == datetime(2026, 11, 18).date()
        ]
        assert [row.room_temp for row in day_ahead] == [
            row.room_temp for row in live_tomorrow
        ]


def test_a_later_run_the_same_day_rewrites_tomorrows_day_ahead_rows(
    client: MariaDBClient,
) -> None:
    # Given a run at 14:10 on a reading of 18 C
    seed_plan_inputs(client, room=18.0)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # When a run an hour later reads a different room temperature
    later = NOW + timedelta(hours=1)
    seed_reading(client, later - timedelta(minutes=5), room=20.0)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(later)

    # Then tomorrow's day-ahead rows are the second run's, still every 10 minutes
    day_ahead = day_ahead_rows(client, "current")
    assert len(day_ahead) == TEN_MINUTE_ROWS_A_DAY
    live_tomorrow = [
        row for row in live_rows(client, "current") if row.starts_at.day == 18
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
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(last_run)
    frozen = [
        (row.starts_at, row.room_temp, row.computed_at)
        for row in day_ahead_rows(client, "current")
    ]
    assert len(frozen) == TEN_MINUTE_ROWS_A_DAY

    # When a run after midnight reads a different room temperature
    after_midnight = datetime(2026, 11, 18, 0, 20, tzinfo=UTC)
    seed_reading(client, after_midnight - timedelta(minutes=5), room=21.0)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(after_midnight)

    # Then Wednesday's day-ahead rows are as the 23:30 run wrote them, Thursday's
    # are written, and nothing is stored for Tuesday
    day_ahead = day_ahead_rows(client, "current")
    assert [
        (row.starts_at, row.room_temp, row.computed_at)
        for row in day_ahead
        if row.starts_at.day == 18
    ] == frozen
    assert [
        row.starts_at for row in day_ahead if row.starts_at.day == 19
    ] == every_ten_minutes(19)
    assert not [row for row in day_ahead if row.starts_at.day == 17]


def test_a_skipped_run_leaves_the_day_ahead_rows_untouched(
    client: MariaDBClient,
) -> None:
    # Given day-ahead rows from a run
    seed_plan_inputs(client, room=18.0)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)
    before = [
        (row.starts_at, row.room_temp, row.computed_at)
        for row in day_ahead_rows(client, "current")
    ]

    # When a later run finds the newest reading stale
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW + timedelta(hours=1))

    # Then the day-ahead rows are as they were
    assert [
        (row.starts_at, row.room_temp, row.computed_at)
        for row in day_ahead_rows(client, "current")
    ] == before
    assert len(before) == TEN_MINUTE_ROWS_A_DAY


def test_a_run_cut_short_by_a_weather_gap_leaves_the_day_ahead_whole(
    client: MariaDBClient,
) -> None:
    # Given a full day-ahead stored for Wednesday by a run on Tuesday
    seed_plan_inputs(client)
    seed_comfort(client, NOW.date(), 19.0)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)
    before = [
        (row.starts_at, row.room_temp, row.computed_at)
        for row in day_ahead_rows(client, "plan")
    ]
    assert len(before) == TEN_MINUTE_ROWS_A_DAY

    # When the weather forecast now stops at 18:00 on Wednesday and the job runs
    # again
    with client.session_write_scope() as session:
        session.execute(
            weather_forecast_hour_table.delete().where(
                weather_forecast_hour_table.c.forecast_at > datetime(2026, 11, 18, 18)
            )
        )
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW + timedelta(minutes=10))

    # Then the live forecast stops at the gap, but the frozen comparison is not
    # shortened, in either scenario
    assert live_rows(client, "plan")[-1].starts_at == datetime(2026, 11, 18, 17, 50)
    assert [
        (row.starts_at, row.room_temp, row.computed_at)
        for row in day_ahead_rows(client, "plan")
    ] == before
    assert len(day_ahead_rows(client, "current")) == TEN_MINUTE_ROWS_A_DAY

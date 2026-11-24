"""The Room Forecast job: the thermal model run forward from the room's newest
reading to the end of tomorrow in 2-minute steps, stored every 10 minutes, under the
Hive schedule and under the Comfort Plan (ADR-0035, ADR-0036).

London is on GMT in November, so local time is UTC."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from common.local_day import set_local_timezone
from octopus_app.common.config import ComfortWindows
from octopus_app.data.mysql.client import MariaDBClient, weather_forecast_hour_table
from octopus_app.data.room_forecast_job import RoomForecastJob

from .room_forecast_fixtures import (  # noqa: F401  # pylint: disable=unused-import
    DAYS,
    NOW,
    ONE_HOUR,
    PINNED_WINDOWS,
    REGION,
    live_rows,
    seed_comfort,
    seed_gas_rate,
    seed_heating_forecast_inputs,
    seed_measured_intervals,
    seed_plan_inputs,
    seed_reading,
    seed_schedule,
    seed_weather_forecast,
    seed_weather_stamps,
    thermal_model_client,
    usable_model,
    weekly_schedule,
)


def test_forecast_under_the_current_schedule_runs_from_now_to_the_end_of_tomorrow(
    client: MariaDBClient,
) -> None:
    # Given a usable model, a weekly schedule at 7 C, an hourly weather forecast
    # and a fresh reading of 18 C
    seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then live `current` rows run every 10 minutes from the one containing the
    # newest reading to the end of tomorrow, starting from that reading
    rows = live_rows(client, "current")
    assert rows[0].starts_at == datetime(2026, 11, 17, 14, 0)
    assert rows[-1].starts_at == datetime(2026, 11, 18, 23, 50)
    assert len(rows) == 34 * 6
    assert rows[0].room_temp == pytest.approx(18.0)
    # nothing is heating, so the room cools by 0.02 * (10 - 18) + 0.08 = -0.08 C an
    # hour, and 14:10 is three 2-minute steps (14:04, 14:06, 14:08) after the
    # reading: 18 - 3 * 2/60 * 0.08 = 17.992 C
    assert rows[1].starts_at == datetime(2026, 11, 17, 14, 10)
    assert rows[1].room_temp == pytest.approx(17.992, abs=1e-4)


def test_a_slot_target_above_the_room_is_held_with_the_least_heating(
    client: MariaDBClient,
) -> None:
    # Given Tuesday's slot targets 19 C and the room is at 18 C
    seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    seed_schedule(client, weekly_schedule({"tuesday": 19.0}))
    seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the heating stops once the room is at 19 C, at 14:50. The heat alone
    # would need about (19 - 18 + 0.07) / 1.3 of an hour, 49 minutes; it stores
    # 45.4, because heat that has arrived keeps warming the room (the model's
    # carried 0.3 C an hour of heating, for the hour after it)
    rows = live_rows(client, "current")
    assert sum(row.on_minutes for row in rows[:5]) == pytest.approx(45.4, abs=0.5)
    assert rows[5].starts_at == datetime(2026, 11, 17, 14, 50)
    assert rows[5].room_temp == pytest.approx(19.0, abs=0.01)
    assert rows[5].on_minutes == 0


def test_heating_kwh_and_cost_follow_the_learned_boiler_kw_and_the_gas_rate(
    client: MariaDBClient,
) -> None:
    # Given the boiler has been measured at 6 kW and gas costs 6 p per kWh
    seed_heating_forecast_inputs(client)
    seed_measured_intervals(client, count=10, kw=6.0)
    seed_gas_rate(client, unit_rate_p=6.0)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the first row's 6 minutes (14:04 to 14:10) is 6/60 * 6 = 0.6 kWh, costing
    # 0.6 * 6 / 100 = 0.036 pounds
    first = live_rows(client, "current")[0]
    assert first.on_minutes == 6.0
    assert first.heating_kwh == pytest.approx(0.6)
    assert first.cost_gbp == pytest.approx(0.036)


def test_a_model_fitted_hourly_forecasts_the_same_room_each_hour_as_the_hourly_forecast(
    client: MariaDBClient,
) -> None:
    # Given the stored model was fitted on hourly steps (no step length), a
    # schedule on whole hours at 7 C and a reading of 18 C at 14:00
    seed_reading(client, NOW - timedelta(minutes=10), room=18.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 14), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the room at each whole hour is what the hourly forecast gave, each hour
    # changing it by 0.02 * (10 - room) + 0.08, to a few thousandths of a degree:
    # 30 steps follow the room's curve a little more closely than one
    by_start = {row.starts_at: row.room_temp for row in live_rows(client, "current")}
    room = 18.0
    for hour in range(15, 20):
        room += 0.02 * (10 - room) + 0.08
        assert by_start[datetime(2026, 11, 17, hour)] == pytest.approx(room, abs=5e-3)


def test_mode_off_forecasts_no_heating(client: MariaDBClient) -> None:
    # Given Hive's mode is OFF, though the schedule asks for 19 C
    seed_reading(client, NOW - timedelta(minutes=5), room=18.0, mode="OFF")
    seed_schedule(client, weekly_schedule({"tuesday": 19.0}))
    seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then no row heats, and the room cools to 17.992 C at 14:10
    rows = live_rows(client, "current")
    assert {row.on_minutes for row in rows} == {0}
    assert rows[1].room_temp == pytest.approx(17.992, abs=1e-4)


def test_manual_at_the_away_temperature_holds_it_over_a_schedule_that_asks_for_more(
    client: MariaDBClient,
) -> None:
    # Given the newest poll is MANUAL at 7 C with no boost, though the schedule
    # asks for 19 C on Tuesday
    seed_reading(
        client, NOW - timedelta(minutes=5), room=18.0, mode="MANUAL", target_temp=7.0
    )
    seed_schedule(client, weekly_schedule({"tuesday": 19.0}))
    seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the current scenario holds 7 C for the whole horizon: no row heats,
    # and the room cools to 17.992 C at 14:10
    rows = live_rows(client, "current")
    assert {row.on_minutes for row in rows} == {0}
    assert rows[1].room_temp == pytest.approx(17.992, abs=1e-4)


def test_a_hand_set_manual_target_is_held_over_a_schedule_that_asks_for_less(
    client: MariaDBClient,
) -> None:
    # Given the newest poll is MANUAL at 21 C set by hand, though the schedule
    # asks for only 7 C all day
    seed_reading(
        client, NOW - timedelta(minutes=5), room=18.0, mode="MANUAL", target_temp=21.0
    )
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the current scenario heats towards 21 C at once, and is still holding
    # it at the end of tomorrow, when the 7 C schedule would have let it cool
    rows = live_rows(client, "current")
    assert rows[0].on_minutes > 0
    assert rows[-1].room_temp == pytest.approx(21.0, abs=0.1)


@pytest.mark.parametrize(
    ("mode", "boost"),
    [("SCHEDULE", False), ("MANUAL", True)],
    ids=["schedule", "manual with a boost running"],
)
def test_schedule_mode_or_a_boost_still_follows_the_hive_schedule(
    client: MariaDBClient, mode: str, boost: bool
) -> None:
    # Given the newest poll's stored target is 7 C, but its mode is SCHEDULE, or
    # it is MANUAL with a boost running, and the schedule asks for 19 C on Tuesday
    seed_reading(
        client,
        NOW - timedelta(minutes=5),
        room=18.0,
        mode=mode,
        target_temp=7.0,
        boost_active=boost,
    )
    seed_schedule(client, weekly_schedule({"tuesday": 19.0}))
    seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the current scenario follows the schedule: it heats towards 19 C, as
    # it does with no stored target in play
    rows = live_rows(client, "current")
    assert sum(row.on_minutes for row in rows[:5]) == pytest.approx(45.4, abs=0.5)


@pytest.mark.parametrize(
    "schedule",
    [None, {"monday": "not slots"}, {day: [{"start": "0"}] for day in DAYS}],
    ids=["missing", "not slots", "slot without a target"],
)
def test_an_unreadable_or_missing_schedule_stores_no_current_rows(
    client: MariaDBClient, caplog: pytest.LogCaptureFixture, schedule: Any
) -> None:
    # Given a fresh reading and weather, but no weekly schedule Hive could be
    # read as
    seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    if schedule is not None:
        seed_schedule(client, schedule)
    seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 14),
        last=datetime(2026, 11, 19, 0),
    )

    # When the job runs
    with caplog.at_level("WARNING"):
        RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then nothing is stored, rather than a forecast of no heating, and a warning
    # says why
    assert live_rows(client, "current") == []
    assert "schedule" in caplog.text


def test_a_second_run_replaces_the_earlier_live_rows(client: MariaDBClient) -> None:
    # Given a first run from a room of 18 C
    seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # When a fresher reading of 20 C arrives and the job runs again
    later = NOW + timedelta(minutes=10)
    seed_reading(client, later - timedelta(minutes=1), room=20.0)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(later)

    # Then only the second run's rows are stored
    rows = live_rows(client, "current")
    assert len(rows) == 34 * 6 - 1  # from the 14:10 row, which holds the reading
    assert rows[0].starts_at == datetime(2026, 11, 17, 14, 10)
    assert rows[0].room_temp == pytest.approx(20.0)


def test_cost_is_empty_without_a_gas_rate(client: MariaDBClient) -> None:
    # Given no gas rate and no measured hours, so the default 12 kW boiler
    seed_heating_forecast_inputs(client)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the energy is estimated, 6 minutes at 12 kW, and there is no cost
    first = live_rows(client, "current")[0]
    assert first.heating_kwh == pytest.approx(1.2)
    assert first.cost_gbp is None


def test_a_reading_more_than_30_minutes_old_stores_nothing(
    client: MariaDBClient,
) -> None:
    # Given a forecast stored from a fresh reading at 14:05
    seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # When the job runs an hour later and no newer reading has arrived, so the
    # newest is 65 minutes old
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW + ONE_HOUR)

    # Then nothing new is stored and the earlier rows are left as they were
    rows = live_rows(client, "current")
    assert rows[0].starts_at == datetime(2026, 11, 17, 14)
    assert len(rows) == 34 * 6


def test_a_reading_exactly_30_minutes_old_is_fresh(client: MariaDBClient) -> None:
    # Given the newest reading is exactly 30 minutes old, at 13:40
    seed_reading(client, NOW - timedelta(minutes=30), room=18.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 13), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then it counts as fresh and the forecast is stored, from the 13:40 row
    # holding the reading, so 20 minutes short of 35 hours
    rows = live_rows(client, "current")
    assert rows[0].starts_at == datetime(2026, 11, 17, 13, 40)
    assert len(rows) == 35 * 6 - 4


def test_an_unusable_newest_model_stores_nothing_and_never_falls_back(
    client: MariaDBClient,
) -> None:
    # Given a forecast stored under the usable model, then a newer model that is
    # not usable
    seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)
    unusable = replace(
        usable_model(),
        fitted_at=NOW - timedelta(hours=1),
        usable=False,
        unusable_reason="too few heating steps",
    )
    client.write_thermal_model(unusable)
    seed_reading(client, NOW + timedelta(minutes=9), room=21.0)

    # When the job runs again
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW + timedelta(minutes=10))

    # Then the older usable model is not used and the earlier rows are as they were
    rows = live_rows(client, "current")
    assert len(rows) == 34 * 6
    assert rows[0].room_temp == pytest.approx(18.0)


def test_no_model_at_all_stores_nothing(mariadb_client: MariaDBClient) -> None:
    # Given a fresh reading, a schedule and weather, but no thermal model
    seed_heating_forecast_inputs(mariadb_client)

    # When the job runs
    RoomForecastJob(mariadb_client, REGION).refresh(NOW)

    # Then nothing is stored
    assert live_rows(mariadb_client, "current") == []


@pytest.mark.parametrize(
    "later",
    [
        datetime(2026, 11, 19, 0, 10, tzinfo=UTC),
        datetime(2026, 11, 19, 1, 10, tzinfo=UTC),
    ],
    ids=["no radiation stamp for the first hour", "no stamp for the first hour"],
)
def test_no_weather_for_the_first_hour_leaves_the_earlier_rows(
    client: MariaDBClient, later: datetime
) -> None:
    # Given a forecast stored earlier, and weather that ends at 00:00 on the 19th
    seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)
    seed_reading(client, later - timedelta(minutes=1), room=20.0)

    # When the job runs at a time whose first hour (or its radiation) has no weather
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(later)

    # Then the earlier rows are not deleted or replaced
    rows = live_rows(client, "current")
    assert len(rows) == 34 * 6
    assert rows[0].room_temp == pytest.approx(18.0)


def test_a_weather_gap_later_in_the_horizon_stops_the_forecast_there(
    client: MariaDBClient,
) -> None:
    # Given a forecast stored earlier, then weather only to 20:00 for the next run
    seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)
    seed_reading(client, NOW + timedelta(minutes=9), room=20.0)
    with client.session_write_scope() as session:
        session.execute(
            weather_forecast_hour_table.delete().where(
                weather_forecast_hour_table.c.forecast_at > datetime(2026, 11, 17, 20)
            )
        )

    # When the job runs again
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW + timedelta(minutes=10))

    # Then the rows run up to the last hour whose radiation is known, replacing
    # the earlier ones
    rows = live_rows(client, "current")
    assert rows[-1].starts_at == datetime(2026, 11, 17, 19, 50)
    assert len(rows) == 6 * 6 - 1
    assert rows[0].room_temp == pytest.approx(20.0)


def test_an_unknown_radiation_counts_as_no_sunshine_and_keeps_the_hour(
    client: MariaDBClient,
) -> None:
    # Given a forecast whose radiation is unknown
    seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_stamps(
        client,
        [
            (datetime(2026, 11, 17, 14) + ONE_HOUR * index, 10.0, None)
            for index in range(36)
        ],
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then every hour is kept and the room cools as with no sunshine, to 17.992 C
    # at 14:10
    rows = live_rows(client, "current")
    assert len(rows) == 34 * 6
    assert rows[1].room_temp == pytest.approx(17.992, abs=1e-4)


def test_an_hours_temperature_pairs_with_the_next_stamps_radiation(
    client: MariaDBClient,
) -> None:
    # Given 10 C and no sunshine at 14:00, and 14 C and 1000 W/m2 at 15:00
    seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_stamps(
        client,
        [
            (datetime(2026, 11, 17, 14), 10.0, 0.0),
            (datetime(2026, 11, 17, 15), 14.0, 1000.0),
        ]
        + [
            (datetime(2026, 11, 17, 16) + ONE_HOUR * index, 10.0, 0.0)
            for index in range(34)
        ],
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the 14:00 hour takes 15:00's 1 kW/m2, and its outdoor temperature runs
    # in a straight line from its own 10 C to 15:00's 14 C: three steps, at 14:04,
    # 14:06 and 14:08, each changing the room by 2/60 * (0.02 * (outdoor - room)
    # + 0.4 * 1 + 0.08)
    room = 18.0
    for minute in (4, 6, 8):
        outdoor = 10.0 + 4.0 * minute / 60
        room += 2 / 60 * (0.02 * (outdoor - room) + 0.4 * 1.0 + 0.08)
    rows = live_rows(client, "current")
    assert rows[1].room_temp == pytest.approx(room)


@pytest.mark.parametrize(
    ("now", "rows_stored", "last_row"),
    [
        # tomorrow, Sunday 25 October, has 25 hours (clocks go back)
        (
            datetime(2026, 10, 24, 14, 10, tzinfo=UTC),
            34 * 6,
            datetime(2026, 10, 25, 23, 50),
        ),
        # tomorrow, Sunday 29 March, has 23 hours (clocks go forward)
        (
            datetime(2026, 3, 28, 14, 10, tzinfo=UTC),
            33 * 6,
            datetime(2026, 3, 29, 22, 50),
        ),
    ],
    ids=["25-hour day", "23-hour day"],
)
def test_the_horizon_ends_at_the_last_hour_of_tomorrows_local_day(
    client: MariaDBClient, now: datetime, rows_stored: int, last_row: datetime
) -> None:
    # Given London time and the day before a clock change
    set_local_timezone("Europe/London")
    first = now.replace(minute=0, tzinfo=None)
    seed_reading(client, now - timedelta(minutes=5), room=18.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(client, first=first, last=first + ONE_HOUR * 40)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(now)

    # Then the rows end at the last 10 minutes of tomorrow's local day
    rows = live_rows(client, "current")
    assert rows[-1].starts_at == last_row
    assert len(rows) == rows_stored


@pytest.mark.parametrize(
    ("now", "rows_stored", "after_opening"),
    [
        # Saturday 24 October to Sunday 25 October, 25 hours: 07:44 is 07:44 UTC
        (
            datetime(2026, 10, 24, 14, 10, tzinfo=UTC),
            34 * 6,
            datetime(2026, 10, 25, 7, 50),
        ),
        # Saturday 28 March to Sunday 29 March, 23 hours: 07:44 BST is 06:44 UTC
        (
            datetime(2026, 3, 28, 14, 10, tzinfo=UTC),
            33 * 6,
            datetime(2026, 3, 29, 6, 50),
        ),
    ],
    ids=["25-hour day", "23-hour day"],
)
def test_the_plan_reaches_its_target_at_the_local_window_start_across_a_clock_change(
    client: MariaDBClient, now: datetime, rows_stored: int, after_opening: datetime
) -> None:
    # Given London time, a comfort temperature of 19 C and the day before a clock
    # change; both days are weekend days, so the window opens at 07:44 local
    set_local_timezone("Europe/London")
    first = now.replace(minute=0, tzinfo=None)
    seed_reading(client, now - timedelta(minutes=5), room=18.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(client, first=first, last=first + ONE_HOUR * 40)
    seed_comfort(client, now.date(), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(now)

    # Then the plan covers the same rows as the current ones, and the room is at
    # 19 C at the first 10-minute row after 07:44 local
    rows = live_rows(client, "plan")
    assert len(rows) == rows_stored
    assert rows[-1].starts_at == live_rows(client, "current")[-1].starts_at
    by_start = {row.starts_at: row for row in rows}
    assert by_start[after_opening].room_temp == pytest.approx(19.0, abs=0.01)


def test_missing_hive_tables_store_nothing_and_do_not_raise(
    mariadb_client_without_weather_tables: MariaDBClient,
) -> None:
    # Given hive-app has never run, so its tables do not exist
    mariadb_client_without_weather_tables.write_thermal_model(usable_model())

    # When the job runs
    RoomForecastJob(mariadb_client_without_weather_tables, REGION).refresh(NOW)

    # Then nothing is stored
    assert live_rows(mariadb_client_without_weather_tables, "current") == []


def test_the_plan_holds_the_rounded_up_comfort_temperature_with_the_shortest_preheat(
    client: MariaDBClient,
) -> None:
    # Given a comfort temperature of 19.3 C today
    seed_plan_inputs(client)
    seed_comfort(client, NOW.date(), 19.3)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the plan covers the same hours as the current rows
    rows = live_rows(client, "plan")
    assert [row.starts_at for row in rows] == [
        row.starts_at for row in live_rows(client, "current")
    ]
    # and heats the room from 18 C flat out from the 14:04 step, already in today's
    # window, up to 19.5 C (not 19.3 C): the first row's 6 minutes and then every
    # 10-minute row full
    assert rows[0].on_minutes == 6.0
    assert [row.on_minutes for row in rows[1:5]] == [10.0] * 4
    by_start = {row.starts_at: row for row in rows}
    day = datetime(2026, 11, 18)
    # tomorrow the room cools overnight while held at 19.5 C to midnight, to 18.89 C
    # at 06:00 (outside is 10 C). It needs 19.5 C at 06:30, so the pre-heat starts
    # at 06:00: flat out for two rows (20 minutes) and 9 minutes of the third,
    # ending with the room at 19.5 C at 06:30, as the carried heat completes the
    # climb; nothing before it or after it needs heat
    assert by_start[day.replace(hour=5, minute=50)].on_minutes == 0
    assert by_start[day.replace(hour=6)].room_temp == pytest.approx(18.89, abs=1e-2)
    assert [
        by_start[day.replace(hour=6, minute=minute)].on_minutes
        for minute in (0, 10, 20)
    ] == [10.0, 10.0, 9.0]
    assert by_start[day.replace(hour=6, minute=30)].room_temp == pytest.approx(
        19.5, abs=1e-3
    )
    assert by_start[day.replace(hour=6, minute=30)].on_minutes == 0


def test_the_plan_holds_the_raised_value_after_a_held_boost(
    client: MariaDBClient,
) -> None:
    # Given today's Comfort Temperature is 19.0, raised by a Held Boost at 13:30
    seed_plan_inputs(client)
    seed_comfort(
        client,
        NOW.date(),
        19.0,
        raised_at=datetime(2026, 11, 17, 13, 30),
        quiet_count=0,
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then today's plan slot holds 19.5
    assert [
        slot.target for slot in client.read_room_forecast_slots("plan", NOW.date())
    ] == [19.5]


def test_the_plan_holds_a_stepped_value_as_it_is(client: MariaDBClient) -> None:
    # Given today's Comfort Temperature is 19.5 under the stepped rule
    seed_plan_inputs(client)
    seed_comfort(client, NOW.date(), 19.5, quiet_count=1)

    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    assert [
        slot.target for slot in client.read_room_forecast_slots("plan", NOW.date())
    ] == [19.5]


def test_the_weekend_plan_starts_at_0744(client: MariaDBClient) -> None:
    # Given it is Friday afternoon, so tomorrow is a Saturday, and a comfort
    # temperature of 19 C
    friday = datetime(2026, 11, 20, 14, 10, tzinfo=UTC)
    seed_reading(client, friday - timedelta(minutes=5), room=18.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 20, 14), last=datetime(2026, 11, 22, 0)
    )
    seed_comfort(client, friday.date(), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(friday)

    # Then the room is at 19 C at 07:50, the first row after the 07:44 opening
    # (the household's 07:45, rounded earlier to a 2-minute mark), and the pre-heat
    # has not started before 06:30
    by_start = {row.starts_at: row for row in live_rows(client, "plan")}
    assert by_start[datetime(2026, 11, 21, 7, 50)].room_temp == pytest.approx(
        19.0, abs=0.01
    )
    assert all(
        row.on_minutes == 0
        for start, row in by_start.items()
        if datetime(2026, 11, 21, 0, 0) <= start < datetime(2026, 11, 21, 6, 30)
    )


def test_a_comfort_window_from_the_config_moves_the_plan(client: MariaDBClient) -> None:
    # Given a configured weekday window that starts at 09:00 on the hour
    seed_plan_inputs(client)
    seed_comfort(client, NOW.date(), 19.0)
    windows = ComfortWindows.model_validate(
        {"weekday": {"start": "09:00", "end": "00:00"}}
    )

    # When the job runs with it
    RoomForecastJob(client, REGION, windows).refresh(NOW)

    # Then tomorrow's room is 19 C at the start of the 09:00 hour, the first held
    # hour, the pre-heat ending in the hour before
    by_hour = {row.starts_at: row for row in live_rows(client, "plan")}
    assert by_hour[datetime(2026, 11, 18, 9)].room_temp == pytest.approx(19.0, abs=1e-3)


def test_no_comfort_temperature_stores_no_plan_but_the_current_rows(
    client: MariaDBClient,
) -> None:
    # Given no comfort_day row at all
    seed_plan_inputs(client)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then no plan rows are stored and the current rows are
    assert live_rows(client, "plan") == []
    assert len(live_rows(client, "current")) == 34 * 6


def test_a_day_with_no_comfort_temperature_uses_the_newest_earlier_one(
    client: MariaDBClient,
) -> None:
    # Given today's row has no temperature but yesterday's is 19 C
    seed_plan_inputs(client)
    seed_comfort(client, NOW.date() - timedelta(days=1), 19.0)
    seed_comfort(client, NOW.date(), None)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the plan holds 19 C: the first row heats 18 C flat out from the 14:04
    # step, 6 minutes
    assert live_rows(client, "plan")[0].on_minutes == 6.0


def test_plan_hours_carry_kwh_and_cost_like_the_current_rows(
    client: MariaDBClient,
) -> None:
    # Given the boiler measured at 6 kW and gas at 6 p per kWh
    seed_plan_inputs(client)
    seed_comfort(client, NOW.date(), 19.0)
    seed_measured_intervals(client, count=10, kw=6.0)
    seed_gas_rate(client, unit_rate_p=6.0)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the first plan row, 6 minutes, is 0.6 kWh and 0.036 pounds
    first = live_rows(client, "plan")[0]
    assert first.on_minutes == 6.0
    assert first.heating_kwh == pytest.approx(0.6)
    assert first.cost_gbp == pytest.approx(0.036)


def test_an_unreadable_schedule_stores_the_plan_and_no_current_rows(
    client: MariaDBClient,
) -> None:
    # Given a fresh reading, weather and comfort temperature, but a schedule that
    # cannot be read
    seed_reading(client, NOW - timedelta(minutes=5), room=18.0)
    seed_schedule(client, {"monday": "not slots"})
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 14), last=datetime(2026, 11, 19, 0)
    )
    seed_comfort(client, NOW.date(), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the plan is stored on its own inputs
    assert live_rows(client, "current") == []
    assert len(live_rows(client, "plan")) == 34 * 6


def test_mode_off_does_not_stop_the_plan(client: MariaDBClient) -> None:
    # Given Hive's mode is OFF
    seed_reading(client, NOW - timedelta(minutes=5), room=18.0, mode="OFF")
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 14), last=datetime(2026, 11, 19, 0)
    )
    seed_comfort(client, NOW.date(), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then the current rows do not heat but the plan does
    assert {row.on_minutes for row in live_rows(client, "current")} == {0}
    assert live_rows(client, "plan")[0].on_minutes == 6.0


@pytest.mark.parametrize("fault", ["stale reading", "no weather", "no model"])
def test_nothing_usable_stores_no_plan_either(
    mariadb_client: MariaDBClient, fault: str
) -> None:
    # Given a comfort temperature and a fault in the forecast's own inputs
    if fault != "no model":
        mariadb_client.write_thermal_model(usable_model())
    reading_age = timedelta(minutes=45 if fault == "stale reading" else 5)
    seed_reading(mariadb_client, NOW - reading_age, room=18.0)
    seed_schedule(mariadb_client, weekly_schedule())
    if fault != "no weather":
        seed_weather_forecast(
            mariadb_client,
            first=datetime(2026, 11, 17, 14),
            last=datetime(2026, 11, 19),
        )
    seed_comfort(mariadb_client, NOW.date(), 19.0)

    # When the job runs
    RoomForecastJob(mariadb_client, REGION).refresh(NOW)

    # Then neither scenario is stored
    assert live_rows(mariadb_client, "plan") == []
    assert live_rows(mariadb_client, "current") == []


def test_an_unreadable_schedule_removes_the_earlier_current_rows_and_keeps_the_plan(
    client: MariaDBClient,
) -> None:
    # Given both forecasts stored from a readable schedule
    seed_plan_inputs(client)
    seed_comfort(client, NOW.date(), 19.0)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)
    assert live_rows(client, "current")

    # When Hive's schedule becomes unreadable and the job runs again
    seed_schedule(client, {"monday": "not slots"})
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW + timedelta(minutes=10))

    # Then the old current-schedule line is gone, not left drawn as today's, and
    # the plan is refreshed
    assert live_rows(client, "current") == []
    plan = live_rows(client, "plan")
    assert plan
    assert plan[0].computed_at == (NOW + timedelta(minutes=10)).replace(tzinfo=None)


def test_no_comfort_temperature_removes_the_earlier_plan_rows_and_keeps_the_current(
    client: MariaDBClient,
) -> None:
    # Given both forecasts stored from a comfort temperature of 19 C
    seed_plan_inputs(client)
    seed_comfort(client, NOW.date(), 19.0)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)
    assert live_rows(client, "plan")

    # When the comfort temperature is gone and the job runs again
    seed_comfort(client, NOW.date(), None)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW + timedelta(minutes=10))

    # Then the old plan line is gone and the current-schedule line is refreshed
    assert live_rows(client, "plan") == []
    assert live_rows(client, "current")

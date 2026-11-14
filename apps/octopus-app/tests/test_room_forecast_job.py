"""The Room Forecast job: the thermal model run forward from the room's newest
reading to the end of tomorrow, under the Hive schedule and under the Comfort Plan
(ADR-0035).

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
    REGION,
    live_rows,
    seed_comfort,
    seed_gas_rate,
    seed_heating_forecast_inputs,
    seed_measured_hours,
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
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then live `current` rows run from the hour containing now to the end of
    # tomorrow, starting from the newest reading
    rows = live_rows(client, "current")
    assert rows[0].hour_start == datetime(2026, 11, 17, 14)
    assert rows[-1].hour_start == datetime(2026, 11, 18, 23)
    assert len(rows) == 34
    assert rows[0].room_temp == pytest.approx(18.0)
    # nothing is heating, so the next hour has cooled by
    # 0.02 * (10 - 18) + 0.08 = -0.08 C
    assert rows[1].room_temp == pytest.approx(17.92)


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
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then the first hour heats for the fraction that just reaches 19 C:
    # (19 - 18 + 0.08) / 1.3 = 0.8308 of the hour, which is 50 minutes
    rows = live_rows(client, "current")
    assert rows[0].on_minutes == 50
    assert rows[1].room_temp == pytest.approx(19.0)


def test_heating_kwh_and_cost_follow_the_learned_boiler_kw_and_the_gas_rate(
    client: MariaDBClient,
) -> None:
    # Given the boiler has been measured at 6 kW and gas costs 6 p per kWh
    seed_heating_forecast_inputs(client)
    seed_measured_hours(client, count=5, kw=6.0)
    seed_gas_rate(client, unit_rate_p=6.0)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then 50 minutes is 50/60 * 6 = 5 kWh, costing 5 * 6 / 100 = 0.30 pounds
    first = live_rows(client, "current")[0]
    assert first.heating_kwh == pytest.approx(5.0)
    assert first.cost_gbp == pytest.approx(0.30)


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
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then no hour heats, and the room cools
    rows = live_rows(client, "current")
    assert {row.on_minutes for row in rows} == {0}
    assert rows[1].room_temp == pytest.approx(17.92)


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
        RoomForecastJob(client, REGION).refresh(NOW)

    # Then nothing is stored, rather than a forecast of no heating, and a warning
    # says why
    assert live_rows(client, "current") == []
    assert "schedule" in caplog.text


def test_a_second_run_replaces_the_earlier_live_rows(client: MariaDBClient) -> None:
    # Given a first run from a room of 18 C
    seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION).refresh(NOW)

    # When a fresher reading of 20 C arrives and the job runs again
    later = NOW + timedelta(minutes=10)
    seed_reading(client, later - timedelta(minutes=1), room=20.0)
    RoomForecastJob(client, REGION).refresh(later)

    # Then only the second run's rows are stored
    rows = live_rows(client, "current")
    assert len(rows) == 34
    assert rows[0].room_temp == pytest.approx(20.0)


def test_cost_is_empty_without_a_gas_rate(client: MariaDBClient) -> None:
    # Given no gas rate and no measured hours, so the default 12 kW boiler
    seed_heating_forecast_inputs(client)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then the energy is estimated and there is no cost
    first = live_rows(client, "current")[0]
    assert first.heating_kwh == pytest.approx(10.0)
    assert first.cost_gbp is None


def test_a_reading_more_than_30_minutes_old_stores_nothing(
    client: MariaDBClient,
) -> None:
    # Given a forecast stored from a fresh reading at 14:05
    seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION).refresh(NOW)

    # When the job runs an hour later and no newer reading has arrived, so the
    # newest is 65 minutes old
    RoomForecastJob(client, REGION).refresh(NOW + ONE_HOUR)

    # Then nothing new is stored and the earlier rows are left as they were
    rows = live_rows(client, "current")
    assert rows[0].hour_start == datetime(2026, 11, 17, 14)
    assert len(rows) == 34


def test_a_reading_exactly_30_minutes_old_is_fresh(client: MariaDBClient) -> None:
    # Given the newest reading is exactly 30 minutes old
    seed_reading(client, NOW - timedelta(minutes=30), room=18.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 14), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then it counts as fresh and the forecast is stored
    assert len(live_rows(client, "current")) == 34


def test_an_unusable_newest_model_stores_nothing_and_never_falls_back(
    client: MariaDBClient,
) -> None:
    # Given a forecast stored under the usable model, then a newer model that is
    # not usable
    seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION).refresh(NOW)
    unusable = replace(
        usable_model(),
        fitted_at=NOW - timedelta(hours=1),
        usable=False,
        unusable_reason="too few heating steps",
    )
    client.write_thermal_model(unusable)
    seed_reading(client, NOW + timedelta(minutes=9), room=21.0)

    # When the job runs again
    RoomForecastJob(client, REGION).refresh(NOW + timedelta(minutes=10))

    # Then the older usable model is not used and the earlier rows are as they were
    rows = live_rows(client, "current")
    assert len(rows) == 34
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
    RoomForecastJob(client, REGION).refresh(NOW)
    seed_reading(client, later - timedelta(minutes=1), room=20.0)

    # When the job runs at a time whose first hour (or its radiation) has no weather
    RoomForecastJob(client, REGION).refresh(later)

    # Then the earlier rows are not deleted or replaced
    rows = live_rows(client, "current")
    assert len(rows) == 34
    assert rows[0].room_temp == pytest.approx(18.0)


def test_a_weather_gap_later_in_the_horizon_stops_the_forecast_there(
    client: MariaDBClient,
) -> None:
    # Given a forecast stored earlier, then weather only to 20:00 for the next run
    seed_heating_forecast_inputs(client)
    RoomForecastJob(client, REGION).refresh(NOW)
    seed_reading(client, NOW + timedelta(minutes=9), room=20.0)
    with client.session_write_scope() as session:
        session.execute(
            weather_forecast_hour_table.delete().where(
                weather_forecast_hour_table.c.forecast_at > datetime(2026, 11, 17, 20)
            )
        )

    # When the job runs again
    RoomForecastJob(client, REGION).refresh(NOW + timedelta(minutes=10))

    # Then the rows run up to the last hour whose radiation is known, replacing
    # the earlier ones
    rows = live_rows(client, "current")
    assert rows[-1].hour_start == datetime(2026, 11, 17, 19)
    assert len(rows) == 6
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
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then every hour is kept and the room cools as with no sunshine
    rows = live_rows(client, "current")
    assert len(rows) == 34
    assert rows[1].room_temp == pytest.approx(17.92)


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
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then 14:00 uses its own 10 C with 15:00's 1 kW/m2:
    # 0.02 * (10 - 18) + 0.4 * 1 + 0.08 = 0.32 C
    rows = live_rows(client, "current")
    assert rows[1].room_temp == pytest.approx(18.32)


@pytest.mark.parametrize(
    ("now", "rows_stored", "last_hour"),
    [
        # tomorrow, Sunday 25 October, has 25 hours (clocks go back)
        (datetime(2026, 10, 24, 14, 10, tzinfo=UTC), 34, datetime(2026, 10, 25, 23)),
        # tomorrow, Sunday 29 March, has 23 hours (clocks go forward)
        (datetime(2026, 3, 28, 14, 10, tzinfo=UTC), 33, datetime(2026, 3, 29, 22)),
    ],
    ids=["25-hour day", "23-hour day"],
)
def test_the_horizon_ends_at_the_last_hour_of_tomorrows_local_day(
    client: MariaDBClient, now: datetime, rows_stored: int, last_hour: datetime
) -> None:
    # Given London time and the day before a clock change
    set_local_timezone("Europe/London")
    first = now.replace(minute=0, tzinfo=None)
    seed_reading(client, now - timedelta(minutes=5), room=18.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(client, first=first, last=first + ONE_HOUR * 40)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(now)

    # Then the rows end at the last hour of tomorrow's local day
    rows = live_rows(client, "current")
    assert rows[-1].hour_start == last_hour
    assert len(rows) == rows_stored


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
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then the plan covers the same hours as the current rows
    rows = live_rows(client, "plan")
    assert [row.hour_start for row in rows] == [
        row.hour_start for row in live_rows(client, "current")
    ]
    # and heats the room from 18 C in the first hour, already in today's window,
    # up to 19.5 C (not 19.3 C): (19.5 - 18 + 0.08) / 1.3 of the hour, 73 minutes
    # of heating, so the whole hour
    assert rows[0].on_minutes == 60
    assert rows[1].room_temp == pytest.approx(18.0 - 0.08 + 1.3)
    by_hour = {row.hour_start: row for row in rows}
    # tomorrow the room has cooled overnight and is at 19.5 C halfway through the
    # 06:00 hour, the hour 06:30 falls in, after the shortest pre-heat
    at_start = (
        by_hour[datetime(2026, 11, 18, 6)].room_temp
        + by_hour[datetime(2026, 11, 18, 7)].room_temp
    ) / 2
    assert at_start == pytest.approx(19.5, abs=1e-3)
    # the pre-heat is the 06:00 hour at full heat (the room is 18.9 C at 06:00 and
    # needs 1.2 C more by 06:30), the overnight hours before it do not heat, and
    # 07:00 is held, not heated
    assert by_hour[datetime(2026, 11, 18, 5)].on_minutes == 0
    assert by_hour[datetime(2026, 11, 18, 6)].on_minutes == 60
    assert by_hour[datetime(2026, 11, 18, 7)].on_minutes == 0


def test_the_weekend_plan_starts_at_0745(client: MariaDBClient) -> None:
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
    RoomForecastJob(client, REGION).refresh(friday)

    # Then the room is at 19 C three quarters of the way through the 07:00 hour
    # (07:45, the pre-heat's last hour) and the 08:00 hour is held
    by_hour = {row.hour_start: row for row in live_rows(client, "plan")}
    saturday = datetime(2026, 11, 21)
    seven = by_hour[saturday.replace(hour=7)].room_temp
    eight = by_hour[saturday.replace(hour=8)].room_temp
    assert seven + 0.75 * (eight - seven) == pytest.approx(19.0, abs=1e-3)
    assert eight >= 19.0


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
    by_hour = {row.hour_start: row for row in live_rows(client, "plan")}
    assert by_hour[datetime(2026, 11, 18, 9)].room_temp == pytest.approx(19.0, abs=1e-3)


def test_no_comfort_temperature_stores_no_plan_but_the_current_rows(
    client: MariaDBClient,
) -> None:
    # Given no comfort_day row at all
    seed_plan_inputs(client)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then no plan rows are stored and the current rows are
    assert live_rows(client, "plan") == []
    assert len(live_rows(client, "current")) == 34


def test_a_day_with_no_comfort_temperature_uses_the_newest_earlier_one(
    client: MariaDBClient,
) -> None:
    # Given today's row has no temperature but yesterday's is 19 C
    seed_plan_inputs(client)
    seed_comfort(client, NOW.date() - timedelta(days=1), 19.0)
    seed_comfort(client, NOW.date(), None)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then the plan holds 19 C: the first hour heats 18 C to 19 C, 50 minutes
    assert live_rows(client, "plan")[0].on_minutes == 50


def test_plan_hours_carry_kwh_and_cost_like_the_current_rows(
    client: MariaDBClient,
) -> None:
    # Given the boiler measured at 6 kW and gas at 6 p per kWh
    seed_plan_inputs(client)
    seed_comfort(client, NOW.date(), 19.0)
    seed_measured_hours(client, count=5, kw=6.0)
    seed_gas_rate(client, unit_rate_p=6.0)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then the first plan hour, 50 minutes, is 5 kWh and 0.30 pounds
    first = live_rows(client, "plan")[0]
    assert first.on_minutes == 50
    assert first.heating_kwh == pytest.approx(5.0)
    assert first.cost_gbp == pytest.approx(0.30)


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
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then the plan is stored on its own inputs
    assert live_rows(client, "current") == []
    assert len(live_rows(client, "plan")) == 34


def test_mode_off_does_not_stop_the_plan(client: MariaDBClient) -> None:
    # Given Hive's mode is OFF
    seed_reading(client, NOW - timedelta(minutes=5), room=18.0, mode="OFF")
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 14), last=datetime(2026, 11, 19, 0)
    )
    seed_comfort(client, NOW.date(), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(NOW)

    # Then the current rows do not heat but the plan does
    assert {row.on_minutes for row in live_rows(client, "current")} == {0}
    assert live_rows(client, "plan")[0].on_minutes == 50


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

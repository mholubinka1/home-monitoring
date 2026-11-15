"""The Room Forecast job under the current schedule, following Hive slots to the
minute inside an hour (ADR-0035).

London is on GMT in November, so local time is UTC. The slot used throughout is
made up: 19 C from 05:40 to 06:25 on a Tuesday, 7 C otherwise."""

from datetime import UTC, datetime, timedelta

import pytest

from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.room_forecast_job import RoomForecastJob

from .room_forecast_fixtures import (  # noqa: F401  # pylint: disable=unused-import
    REGION,
    live_rows,
    seed_gas_rate,
    seed_measured_hours,
    seed_reading,
    seed_schedule,
    seed_weather_forecast,
    thermal_model_client,
    tuesday_slot_schedule,
    weekly_schedule,
)

# A Tuesday, ten minutes into the 05:00 hour.
EARLY = datetime(2026, 11, 17, 5, 10, tzinfo=UTC)


def test_a_slot_that_straddles_an_hour_heats_its_own_minutes_in_each_hour(
    client: MariaDBClient,
) -> None:
    # Given a slot at 19 C from 05:40 to 06:25 and a room at 16 C
    seed_reading(client, EARLY - timedelta(minutes=5), room=16.0)
    seed_schedule(client, tuesday_slot_schedule())
    seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 5),
        last=datetime(2026, 11, 19, 0),
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(EARLY)

    # Then the 05:00 hour stores 20 minutes of heating (05:40 to 06:00), the
    # 06:00 hour 25 (06:00 to 06:25), and nothing after
    rows = live_rows(client, "current")
    assert [row.on_minutes for row in rows[:3]] == [20, 25, 0]
    assert all(row.on_minutes == 0 for row in rows[2:])


def seed_straddling_slot(client: MariaDBClient, room: float = 16.0) -> None:
    seed_reading(client, EARLY - timedelta(minutes=5), room=room)
    seed_schedule(client, tuesday_slot_schedule())
    seed_weather_forecast(
        client,
        first=datetime(2026, 11, 17, 5),
        last=datetime(2026, 11, 19, 0),
    )


def test_stored_kwh_and_cost_follow_the_minutes_of_each_hour(
    client: MariaDBClient,
) -> None:
    # Given those 20 and 25 minutes, a boiler measured at 6 kW and gas at 6 p/kWh
    seed_straddling_slot(client)
    seed_measured_hours(client, count=5, kw=6.0)
    seed_gas_rate(client, unit_rate_p=6.0)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(EARLY)

    # Then 20 minutes is 20/60 * 6 = 2 kWh costing 2 * 6 / 100 = 0.12 pounds,
    # and 25 minutes is 25/60 * 6 = 2.5 kWh costing 0.15 pounds
    first, second = live_rows(client, "current")[:2]
    assert (first.heating_kwh, first.cost_gbp) == pytest.approx((2.0, 0.12))
    assert (second.heating_kwh, second.cost_gbp) == pytest.approx((2.5, 0.15))


def test_a_room_that_reaches_the_target_part_way_through_a_slot_stops_heating(
    client: MariaDBClient,
) -> None:
    # Given a room at 18.8 C, close to the slot's 19 C
    seed_straddling_slot(client, room=18.8)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(EARLY)

    # Then, with the 05:00 hour drifting 0.02 * (10 - 18.8) + 0.08 = -0.096 C,
    # the room is 18.8 - 35/60 * 0.096 = 18.744 C at 05:40 (the reading is at
    # 05:05) and needs (19 - 18.744 + 0.032) / 1.3 = 0.2215 of the hour, 13
    # minutes, not the slot's 20: the room is at 19 C at 06:00
    rows = live_rows(client, "current")
    assert rows[0].on_minutes == 13
    assert rows[1].room_temp == pytest.approx(19.0)
    # and 06:00 needs only (0.4167 * 0.033538) / 1.3 of the hour, 1 minute, to
    # hold 19 C to 06:25 (carrying 0.2215 * 0.3 of the earlier heating)
    assert [row.on_minutes for row in rows[1:3]] == [1, 0]


def test_two_slot_changes_inside_an_hour_each_heat_for_at_most_their_own_length(
    client: MariaDBClient,
) -> None:
    # Given 19 C only from 05:20 to 05:50, and a cold room at 16 C
    schedule = weekly_schedule()
    schedule["tuesday"] = [
        {"start": 0, "value": {"target": 7.0}},
        {"start": 5 * 60 + 20, "value": {"target": 19.0}},
        {"start": 5 * 60 + 50, "value": {"target": 7.0}},
    ]
    seed_reading(client, EARLY - timedelta(minutes=5), room=16.0)
    seed_schedule(client, schedule)
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 5), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(EARLY)

    # Then the 05:00 hour is three parts: 20 minutes at 7 C, 30 at 19 C and 10 at
    # 7 C. The room (16 C, above 7 C) heats only in the middle part, which cannot
    # reach 19 C and so heats all 30 minutes; nothing follows
    rows = live_rows(client, "current")
    assert [row.on_minutes for row in rows[:3]] == [30, 0, 0]


def test_mode_off_heats_no_hour_even_in_a_slot_that_straddles_one(
    client: MariaDBClient,
) -> None:
    # Given Hive's mode is OFF, though a slot asks for 19 C from 05:40 to 06:25
    seed_reading(client, EARLY - timedelta(minutes=5), room=16.0, mode="OFF")
    seed_schedule(client, tuesday_slot_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 5), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(EARLY)

    # Then no hour heats
    rows = live_rows(client, "current")
    assert rows
    assert {row.on_minutes for row in rows} == {0}

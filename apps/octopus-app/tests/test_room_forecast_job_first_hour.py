"""The Room Forecast job's first hour: the forecast starts at the newest reading's
time, not at the top of the hour (ADR-0035).

London is on GMT in November, so local time is UTC. The slot is the made-up one:
19 C from 05:40 to 06:25 on a Tuesday, 7 C otherwise."""

from datetime import UTC, date, datetime

import pytest

from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.room_forecast_job import RoomForecastJob

from .room_forecast_fixtures import (  # noqa: F401  # pylint: disable=unused-import
    REGION,
    live_rows,
    seed_comfort,
    seed_heating_polls,
    seed_reading,
    seed_schedule,
    seed_weather_forecast,
    thermal_model_client,
    tuesday_slot_schedule,
    weekly_schedule,
)


def test_a_reading_taken_just_after_a_slot_has_ended_stores_no_heating_for_the_rest_of_the_hour(
    client: MariaDBClient,
) -> None:
    # Given a reading of 18.9 C at 06:30, five minutes before the job runs, after
    # the 05:40 to 06:25 slot at 19 C has ended
    seed_reading(client, datetime(2026, 11, 17, 6, 30, tzinfo=UTC), room=18.9)
    seed_schedule(client, tuesday_slot_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 6), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 6, 35, tzinfo=UTC))

    # Then the rest of the 06:00 hour is at 7 C, below the room, so it stores no
    # heating (treating the reading as the room at 06:00 would heat 19 C for the
    # 25 minutes of the slot, 6.5 minutes), and the row still shows the reading
    rows = live_rows(client, "current")
    assert rows[0].hour_start == datetime(2026, 11, 17, 6)
    assert rows[0].room_temp == pytest.approx(18.9)
    assert rows[0].on_minutes == 0


def test_a_reading_taken_inside_a_slot_stores_only_the_minutes_from_the_reading_to_its_end(
    client: MariaDBClient,
) -> None:
    # Given a reading of 18 C at 06:10, inside the 05:40 to 06:25 slot at 19 C
    seed_reading(client, datetime(2026, 11, 17, 6, 10, tzinfo=UTC), room=18.0)
    seed_schedule(client, tuesday_slot_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 6), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 6, 15, tzinfo=UTC))

    # Then the 06:00 hour heats from 06:10 to 06:25, 15 minutes, not the 25 minutes
    # of the whole slot in that hour: the 15 minutes at 19 C need
    # (19 - 18 + 0.25 * 0.08) / 1.3 = 0.78 of an hour, more than the 0.25 there
    # is, and the 35 minutes after the slot at 7 C need none
    rows = live_rows(client, "current")
    assert rows[0].on_minutes == 15


def test_heating_seen_before_the_reading_is_carried_into_the_next_hour(
    client: MariaDBClient,
) -> None:
    # Given the thermostat called for heat in ten polls (20 minutes) from 06:00,
    # then a reading of 18 C at 06:20, and a 7 C schedule that needs no more heat
    seed_heating_polls(client, datetime(2026, 11, 17, 6), count=10)
    seed_reading(client, datetime(2026, 11, 17, 6, 20, tzinfo=UTC), room=18.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 6), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 6, 25, tzinfo=UTC))

    # Then the 06:00 hour stores no heating to come, and the room drifts the last
    # 40 minutes: 18 + 40/60 * (0.02 * (10 - 18) + 0.08) = 17.9467 C at 07:00
    rows = live_rows(client, "current")
    assert rows[0].on_minutes == 0
    assert rows[1].room_temp == pytest.approx(17.946667)
    # and the 07:00 hour still carries the 20 minutes seen, a third of an hour:
    # 0.02 * (10 - 17.9467) + 0.08 + 0.3 / 3 = 0.0211 C, so 17.9677 C at 08:00
    # (17.8677 C if the seen heating were forgotten)
    assert rows[2].room_temp == pytest.approx(17.967733)


def test_heating_seen_is_no_more_than_the_minutes_of_the_hour_gone_by(
    client: MariaDBClient,
) -> None:
    # Given one poll calling for heat at 06:00, which covers two minutes, and a
    # reading of 18 C at 06:01, one minute into the hour
    seed_heating_polls(client, datetime(2026, 11, 17, 6), count=1)
    seed_reading(client, datetime(2026, 11, 17, 6, 1, tzinfo=UTC), room=18.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 6), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 6, 5, tzinfo=UTC))

    # Then the room is 18 - 59/60 * 0.08 = 17.9213 C at 07:00 and only the one
    # minute gone by is carried: 0.02 * (10 - 17.9213) + 0.08 + 0.3 / 60 =
    # -0.0734 C, so 17.8479 C at 08:00 (17.8529 C with the poll's two minutes)
    rows = live_rows(client, "current")
    assert rows[2].room_temp == pytest.approx(17.847907)


def test_a_reading_in_the_previous_hour_starts_the_rows_at_that_hour(
    client: MariaDBClient,
) -> None:
    # Given a reading of 18 C at 06:50 and a schedule holding 19 C all Tuesday
    seed_reading(client, datetime(2026, 11, 17, 6, 50, tzinfo=UTC), room=18.0)
    seed_schedule(client, weekly_schedule({"tuesday": 19.0}))
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 6), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs at 07:05, fifteen minutes after the reading
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 7, 5, tzinfo=UTC))

    # Then the rows start at 06:00, the hour the reading is in, and that hour
    # heats only the ten minutes after the reading: it needs
    # (19 - 18 + 1/6 * 0.08) / 1.3 = 0.78 of an hour, more than the 1/6 left
    rows = live_rows(client, "current")
    assert rows[0].hour_start == datetime(2026, 11, 17, 6)
    assert rows[0].room_temp == pytest.approx(18.0)
    assert rows[0].on_minutes == 10


def test_the_plans_preheat_is_searched_from_the_reading_not_the_top_of_the_hour(
    client: MariaDBClient,
) -> None:
    # Given a reading of 18.9 C at 06:20, ten minutes before the weekday comfort
    # window opens at 06:30, and a comfort temperature of 19 C
    seed_reading(client, datetime(2026, 11, 17, 6, 20, tzinfo=UTC), room=18.9)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 6), last=datetime(2026, 11, 19, 0)
    )
    seed_comfort(client, date(2026, 11, 17), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 6, 25, tzinfo=UTC))

    # Then the row shows the reading, and the pre-heat is the shortest that has the
    # room at 19 C at 06:30 from 18.9 C at 06:20. The hour drifts
    # 0.02 * (10 - 18.9) + 0.08 = -0.098 C, so the ten minutes before the opening
    # drift -0.0163 C and need (0.1 + 0.0163) / 1.3 = 0.0895 of an hour (5.4
    # minutes); holding 19 C for the last half hour needs 0.5 * 0.098 / 1.3 =
    # 0.0377 more: 0.1272 of an hour is 8 minutes (9 if the search started at
    # 06:00, from a room of 18.9 C then)
    rows = live_rows(client, "plan")
    assert rows[0].room_temp == pytest.approx(18.9)
    assert rows[0].on_minutes == 8


def test_the_plans_preheat_that_starts_before_the_hour_is_out_ends_from_the_reading(
    client: MariaDBClient,
) -> None:
    # Given a reading of 18.3 C at 05:50, forty minutes before the weekday comfort
    # window opens at 06:30, and a comfort temperature of 19 C
    seed_reading(client, datetime(2026, 11, 17, 5, 50, tzinfo=UTC), room=18.3)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 5), last=datetime(2026, 11, 19, 0)
    )
    seed_comfort(client, date(2026, 11, 17), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 5, 55, tzinfo=UTC))

    # Then the lead is searched from the room of 18.3 C at 05:50. Without heating
    # the room is 18.3 - 1/6 * 0.086 = 18.2857 C at 06:00, and the half hour to
    # 06:30 needs 30 minutes of heat and more, so the pre-heat starts x hours
    # before 06:00 where, with 0.3 of x carried into the 06:00 hour,
    # 18.2857 + 1.3x + 0.5 * (-0.0857 + 0.274x) + 0.65 = 19: x = 0.0746 hour, 4.5
    # minutes, the heating of the 05:00 hour (7 if the search began at 05:00, from
    # a room of 18.3 C then)
    rows = live_rows(client, "plan")
    assert rows[0].on_minutes == 4

"""The Room Forecast job's first row: the forecast starts at the step holding the
newest reading, not at the top of the hour, and carries the heat the thermostat
had already asked for (ADR-0035, ADR-0036).

London is on GMT in November, so local time is UTC. The slot is the made-up one:
19 C from 05:40 to 06:25 on a Tuesday, 7 C otherwise."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.room_forecast_job import RoomForecastJob

from .room_forecast_fixtures import (  # noqa: F401  # pylint: disable=unused-import
    NOW,
    REGION,
    live_rows,
    seed_comfort,
    seed_heating_polls,
    seed_reading,
    seed_schedule,
    seed_weather_forecast,
    thermal_model_client,
    tuesday_slot_schedule,
    usable_model,
    weekly_schedule,
)

DAY = datetime(2026, 11, 17)
# A room of 18 C with 10 C outside and no sun settles towards 14 C at 2 % an hour.
UNHEATED_AFTER_TWO_HOURS = 14 + 4 * 0.98**2


def _seed_weather(client: MariaDBClient, first_hour: int) -> None:
    seed_weather_forecast(
        client, first=DAY.replace(hour=first_hour), last=datetime(2026, 11, 19, 0)
    )


def _by_start(client: MariaDBClient, scenario: str = "current") -> dict:
    return {row.starts_at: row for row in live_rows(client, scenario)}


def test_a_reading_taken_just_after_a_slot_has_ended_stores_no_heating_for_the_rest_of_the_hour(
    client: MariaDBClient,
) -> None:
    # Given a reading of 18.9 C at 06:30, five minutes before the job runs, after
    # the 05:40 to 06:25 slot at 19 C has ended
    seed_reading(client, datetime(2026, 11, 17, 6, 30, tzinfo=UTC), room=18.9)
    seed_schedule(client, tuesday_slot_schedule())
    _seed_weather(client, 6)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 6, 35, tzinfo=UTC))

    # Then the rest of the day is at 7 C, below the room, so it stores no heating,
    # and the first row, 06:30, still shows the reading
    rows = live_rows(client, "current")
    assert rows[0].starts_at == datetime(2026, 11, 17, 6, 30)
    assert rows[0].room_temp == pytest.approx(18.9)
    assert {row.on_minutes for row in rows} == {0}


def test_a_reading_taken_inside_a_slot_stores_only_the_minutes_from_the_reading_to_its_end(
    client: MariaDBClient,
) -> None:
    # Given a reading of 18 C at 06:10, inside the 05:40 to 06:25 slot at 19 C
    seed_reading(client, datetime(2026, 11, 17, 6, 10, tzinfo=UTC), room=18.0)
    seed_schedule(client, tuesday_slot_schedule())
    _seed_weather(client, 6)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 6, 15, tzinfo=UTC))

    # Then the 06:10 row heats flat out for its 10 minutes and the 06:20 row for
    # the 4 minutes up to the 06:24 step, where the 7 C slot takes effect: the room
    # needs 1 C, so it cannot reach 19 C in the 14 minutes left
    by_start = _by_start(client)
    assert by_start[datetime(2026, 11, 17, 6, 10)].on_minutes == 10.0
    assert by_start[datetime(2026, 11, 17, 6, 20)].on_minutes == 4.0
    assert by_start[datetime(2026, 11, 17, 6, 30)].on_minutes == 0.0


def test_heating_asked_for_before_the_reading_still_warms_the_room_afterwards(
    client: MariaDBClient,
) -> None:
    # Given the thermostat called for heat in ten polls (20 minutes) from 06:00,
    # then a reading of 18 C at 06:20, and a 7 C schedule that needs no more heat
    seed_heating_polls(client, datetime(2026, 11, 17, 6), count=10)
    seed_reading(client, datetime(2026, 11, 17, 6, 20, tzinfo=UTC), room=18.0)
    seed_schedule(client, weekly_schedule())
    _seed_weather(client, 6)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 6, 25, tzinfo=UTC))

    # Then nothing more is stored for heating, and two hours on the room is about
    # 0.082 C warmer than the unheated course: each minute of heat seen gives the
    # model's carried 0.3 C an hour for the 60 minutes after it, 0.005 C a minute,
    # of which only the part after the reading is left to add: the 20 minutes seen,
    # on average 49 of their 60 minutes still ahead, give 20 * 0.005 * 49 / 60
    by_start = _by_start(client)
    assert {row.on_minutes for row in by_start.values()} == {0}
    assert by_start[datetime(2026, 11, 17, 8, 20)].room_temp == pytest.approx(
        UNHEATED_AFTER_TWO_HOURS + 0.082, abs=0.005
    )


def test_a_model_fitted_on_10_minutes_carries_the_heat_seen_for_10_minutes(
    client: MariaDBClient,
) -> None:
    # Given a newer model fitted on 10-minute steps, the same 20 minutes of heating
    # from 06:00 and a reading of 18 C at 06:20
    client.write_thermal_model(
        replace(usable_model(), fitted_at=NOW + timedelta(hours=1), step_minutes=10)
    )
    seed_heating_polls(client, datetime(2026, 11, 17, 6), count=10)
    seed_reading(client, datetime(2026, 11, 17, 6, 20, tzinfo=UTC), room=18.0)
    seed_schedule(client, weekly_schedule())
    _seed_weather(client, 6)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 6, 25, tzinfo=UTC))

    # Then only the last 10 minutes of that heating still count, as 0.3 C an hour of
    # heating for the 10 minutes after each: in the 2-minute steps from 06:20 the
    # heat arrived in the 5 steps before each is 10, 8, 6, 4 and 2 minutes, which
    # add 0.3 * (2 / 10) * 30 / 60 = 0.03 C by 06:30
    assert _by_start(client)[datetime(2026, 11, 17, 8, 20)].room_temp == pytest.approx(
        UNHEATED_AFTER_TWO_HOURS + 0.029, abs=0.005
    )


def test_heating_asked_for_long_before_the_reading_is_forgotten(
    client: MariaDBClient,
) -> None:
    # Given the thermostat called for heat for 20 minutes from 03:00, three hours
    # before the reading, beyond the hour the model carries heat for
    seed_heating_polls(client, datetime(2026, 11, 17, 3), count=10)
    seed_reading(client, datetime(2026, 11, 17, 6, 20, tzinfo=UTC), room=18.0)
    seed_schedule(client, weekly_schedule())
    _seed_weather(client, 6)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 6, 25, tzinfo=UTC))

    # Then the room follows the unheated course
    assert _by_start(client)[datetime(2026, 11, 17, 8, 20)].room_temp == pytest.approx(
        UNHEATED_AFTER_TWO_HOURS, abs=0.01
    )


def test_a_reading_late_in_an_hour_starts_the_rows_at_its_own_ten_minutes(
    client: MariaDBClient,
) -> None:
    # Given a reading of 18 C at 06:50 and a schedule holding 19 C all Tuesday
    seed_reading(client, datetime(2026, 11, 17, 6, 50, tzinfo=UTC), room=18.0)
    seed_schedule(client, weekly_schedule({"tuesday": 19.0}))
    _seed_weather(client, 6)

    # When the job runs at 07:05, fifteen minutes after the reading
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 7, 5, tzinfo=UTC))

    # Then the rows start at 06:50, which holds the reading and heats flat out
    rows = live_rows(client, "current")
    assert rows[0].starts_at == datetime(2026, 11, 17, 6, 50)
    assert rows[0].room_temp == pytest.approx(18.0)
    assert rows[0].on_minutes == 10.0


def test_the_plans_preheat_is_searched_from_the_reading_not_the_top_of_the_hour(
    client: MariaDBClient,
) -> None:
    # Given a reading of 18.9 C at 06:20, ten minutes before the weekday comfort
    # window opens at 06:30, and a comfort temperature of 19 C
    seed_reading(client, datetime(2026, 11, 17, 6, 20, tzinfo=UTC), room=18.9)
    seed_schedule(client, weekly_schedule())
    _seed_weather(client, 6)
    seed_comfort(client, date(2026, 11, 17), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 6, 25, tzinfo=UTC))

    # Then the first row shows the reading, and the pre-heat is the shortest that
    # has the room at 19 C at 06:30 from 18.9 C at 06:20: the ten minutes before
    # the opening drift -0.0163 C and need (0.1 + 0.0163) / 1.3 of an hour, about
    # 5.4 minutes, started on a 2-minute mark (06:24) and sized to just reach 19 C
    by_start = _by_start(client, "plan")
    first = by_start[datetime(2026, 11, 17, 6, 20)]
    assert first.room_temp == pytest.approx(18.9)
    assert first.on_minutes == pytest.approx(5.3, abs=0.2)
    assert by_start[datetime(2026, 11, 17, 6, 30)].room_temp == pytest.approx(
        19.0, abs=0.01
    )


def test_a_preheat_that_began_before_the_reading_is_searched_from_the_reading(
    client: MariaDBClient,
) -> None:
    # Given a reading of 18.3 C at 05:50, forty minutes before the weekday comfort
    # window opens at 06:30, and a comfort temperature of 19 C
    seed_reading(client, datetime(2026, 11, 17, 5, 50, tzinfo=UTC), room=18.3)
    seed_schedule(client, weekly_schedule())
    _seed_weather(client, 5)
    seed_comfort(client, date(2026, 11, 17), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(datetime(2026, 11, 17, 5, 55, tzinfo=UTC))

    # Then the room is at 19 C at 06:30, and the pre-heat starts after the reading
    # (the 05:50 row is the first and holds the reading)
    by_start = _by_start(client, "plan")
    assert by_start[datetime(2026, 11, 17, 5, 50)].room_temp == pytest.approx(18.3)
    assert by_start[datetime(2026, 11, 17, 6, 30)].room_temp == pytest.approx(
        19.0, abs=0.01
    )
    heated = [
        start
        for start, row in by_start.items()
        if row.on_minutes > 0 and start < datetime(2026, 11, 17, 6, 30)
    ]
    assert heated
    assert min(heated) >= datetime(2026, 11, 17, 5, 50)

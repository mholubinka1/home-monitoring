"""The Room Forecast job under the current schedule, following Hive slots in
2-minute steps and storing a row every 10 minutes (ADR-0035, ADR-0036).

London is on GMT in November, so local time is UTC. The slot used throughout is
made up: 19 C from 05:40 to 06:25 on a Tuesday, 7 C otherwise."""

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import pytest

from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.room_forecast_job import RoomForecastJob

from .room_forecast_fixtures import (  # noqa: F401  # pylint: disable=unused-import
    REGION,
    live_rows,
    seed_gas_rate,
    seed_measured_intervals,
    seed_reading,
    seed_schedule,
    seed_weather_forecast,
    thermal_model_client,
    tuesday_slot_schedule,
    weekly_schedule,
)

# A Tuesday, ten minutes into the 05:00 hour.
EARLY = datetime(2026, 11, 17, 5, 10, tzinfo=UTC)


def _on_minutes_by_start(client: MariaDBClient) -> dict[datetime, float]:
    return {row.starts_at: row.on_minutes for row in live_rows(client, "current")}


def test_a_slot_ending_on_an_odd_minute_heats_every_step_up_to_the_one_it_ends_in(
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

    # Then the 05:40 to 06:10 rows each store 10 minutes of heating, the 06:20 row
    # 4 (the 7 C slot that starts at 06:25 takes effect from the 06:24 step), and
    # no other row any
    on_minutes = _on_minutes_by_start(client)
    day = datetime(2026, 11, 17)
    heated = {
        day.replace(hour=5, minute=40): 10.0,
        day.replace(hour=5, minute=50): 10.0,
        day.replace(hour=6, minute=0): 10.0,
        day.replace(hour=6, minute=10): 10.0,
        day.replace(hour=6, minute=20): 4.0,
    }
    assert {start: on for start, on in on_minutes.items() if on} == heated


def test_every_stored_row_is_ten_minutes_long_and_starts_on_a_ten_minute_mark(
    client: MariaDBClient,
) -> None:
    seed_reading(client, EARLY - timedelta(minutes=5), room=16.0)
    seed_schedule(client, tuesday_slot_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 5), last=datetime(2026, 11, 19, 0)
    )

    RoomForecastJob(client, REGION).refresh(EARLY)

    rows = live_rows(client, "current")
    assert rows[0].starts_at == datetime(2026, 11, 17, 5, 0)
    assert all(row.ends_at - row.starts_at == timedelta(minutes=10) for row in rows)
    assert all(row.starts_at.minute % 10 == 0 for row in rows)
    # one row after another to the end of tomorrow, with no gap
    assert all(later.starts_at == earlier.ends_at for earlier, later in pairwise(rows))
    assert rows[-1].ends_at == datetime(2026, 11, 19, 0, 0)


def test_the_first_row_holds_the_reading_and_only_the_steps_from_its_step_on(
    client: MariaDBClient,
) -> None:
    # Given a reading of 16 C at 05:05, inside the 05:04 step
    seed_reading(client, EARLY - timedelta(minutes=5), room=16.0)
    seed_schedule(client, tuesday_slot_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 5), last=datetime(2026, 11, 19, 0)
    )

    RoomForecastJob(client, REGION).refresh(EARLY)

    first = live_rows(client, "current")[0]
    assert first.starts_at == datetime(2026, 11, 17, 5, 0)
    assert first.room_temp == pytest.approx(16.0)


def test_stored_kwh_and_cost_follow_the_minutes_of_each_row(
    client: MariaDBClient,
) -> None:
    # Given those minutes, a boiler measured at 6 kW and gas at 6 p/kWh
    seed_reading(client, EARLY - timedelta(minutes=5), room=16.0)
    seed_schedule(client, tuesday_slot_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 5), last=datetime(2026, 11, 19, 0)
    )
    seed_measured_intervals(client, count=10, kw=6.0)
    seed_gas_rate(client, unit_rate_p=6.0)

    # When the job runs
    RoomForecastJob(client, REGION).refresh(EARLY)

    # Then 10 minutes is 10/60 * 6 = 1 kWh costing 1 * 6 / 100 = 0.06 pounds, and
    # 4 minutes is 0.4 kWh costing 0.024 pounds
    by_start = {row.starts_at: row for row in live_rows(client, "current")}
    full = by_start[datetime(2026, 11, 17, 5, 40)]
    last = by_start[datetime(2026, 11, 17, 6, 20)]
    assert (full.heating_kwh, full.cost_gbp) == pytest.approx((1.0, 0.06))
    assert (last.heating_kwh, last.cost_gbp) == pytest.approx((0.4, 0.024))


def test_the_gas_unit_rate_is_read_once_per_hour_of_a_run(
    client: MariaDBClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_reading(client, EARLY - timedelta(minutes=5), room=16.0)
    seed_schedule(client, tuesday_slot_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 5), last=datetime(2026, 11, 19, 0)
    )
    seed_gas_rate(client, unit_rate_p=6.0)
    asked: list[datetime] = []
    read_rate = client.read_gas_unit_rate

    def counting_read(region: str, as_of: datetime) -> float | None:
        asked.append(as_of)
        return read_rate(region, as_of)

    monkeypatch.setattr(client, "read_gas_unit_rate", counting_read)

    RoomForecastJob(client, REGION).refresh(EARLY)

    # 5:00 on the 17th to 23:00 on the 18th is 43 hours, read once each
    assert len(asked) == len(set(asked)) == 43


def test_a_room_that_reaches_the_target_part_way_through_a_slot_stops_heating(
    client: MariaDBClient,
) -> None:
    # Given a room at 18.8 C, close to the slot's 19 C
    seed_reading(client, EARLY - timedelta(minutes=5), room=18.8)
    seed_schedule(client, tuesday_slot_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 5), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(EARLY)

    # Then the room, drifting 0.02 * (10 - 18.8) + 0.08 = -0.096 C an hour, is
    # about 18.744 C at 05:40 and needs (19 - 18.744 + 0.02) / 1.3 of an hour, about
    # 13 minutes, not the slot's 44: the first row heats flat out, the next
    # finishes the climb, and the room is at 19 C at 06:00
    rows = {row.starts_at: row for row in live_rows(client, "current")}
    day = datetime(2026, 11, 17)
    assert rows[day.replace(hour=5, minute=40)].on_minutes == 10.0
    assert rows[day.replace(hour=5, minute=50)].on_minutes == pytest.approx(
        3.0, abs=1.0
    )
    assert rows[day.replace(hour=6, minute=0)].room_temp == pytest.approx(
        19.0, abs=1e-2
    )
    # and holding 19 C to 06:24 needs a little heating, well under the 10 minutes a
    # row could take
    assert 0 < rows[day.replace(hour=6, minute=10)].on_minutes < 2.0


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

    # Then heating is flat out for the slot's 30 minutes (05:20 to 05:50) and
    # nothing else
    on_minutes = _on_minutes_by_start(client)
    assert {start: on for start, on in on_minutes.items() if on} == {
        datetime(2026, 11, 17, 5, 20): 10.0,
        datetime(2026, 11, 17, 5, 30): 10.0,
        datetime(2026, 11, 17, 5, 40): 10.0,
    }


def test_a_slot_starting_on_an_odd_minute_heats_from_the_step_before_it(
    client: MariaDBClient,
) -> None:
    # Given 19 C from 05:41 to 06:00 and a cold room
    schedule = weekly_schedule()
    schedule["tuesday"] = [
        {"start": 0, "value": {"target": 7.0}},
        {"start": 5 * 60 + 41, "value": {"target": 19.0}},
        {"start": 6 * 60, "value": {"target": 7.0}},
    ]
    seed_reading(client, EARLY - timedelta(minutes=5), room=16.0)
    seed_schedule(client, schedule)
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 5), last=datetime(2026, 11, 19, 0)
    )

    RoomForecastJob(client, REGION).refresh(EARLY)

    # Then the 05:40 step is heated, so the 05:40 row holds 10 minutes
    assert _on_minutes_by_start(client)[datetime(2026, 11, 17, 5, 40)] == 10.0


def test_mode_off_heats_no_row_even_in_a_slot(client: MariaDBClient) -> None:
    # Given Hive's mode is OFF, though a slot asks for 19 C from 05:40 to 06:25
    seed_reading(client, EARLY - timedelta(minutes=5), room=16.0, mode="OFF")
    seed_schedule(client, tuesday_slot_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 5), last=datetime(2026, 11, 19, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION).refresh(EARLY)

    # Then no row heats
    rows: list[Any] = live_rows(client, "current")
    assert rows
    assert {row.on_minutes for row in rows} == {0}

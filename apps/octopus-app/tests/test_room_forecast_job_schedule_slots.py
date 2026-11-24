"""The Room Forecast job stores the day's Hive schedule slots for the dashboard's
schedule table (feature-schedule-table-panel).

London is on GMT in November, so local time is UTC. The schedule is made up: 7 C
all week, except Tuesday's slot at 19 C from 05:40 to 06:25."""

from datetime import UTC, datetime, time, timedelta
from itertools import pairwise

from octopus_app.common.config import ComfortWindow, ComfortWindows
from octopus_app.data.model import RoomForecastSlotRecord
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.room_forecast_job import RoomForecastJob

from . import room_forecast_fixtures
from .room_forecast_fixtures import (  # noqa: F401  # pylint: disable=unused-import
    PINNED_WINDOWS,
    REGION,
    SLOT_END,
    SLOT_START,
    live_rows,
    seed_comfort,
    seed_gas_rate,
    seed_plan_inputs,
    seed_reading,
    seed_schedule,
    seed_weather_forecast,
    slot_rows,
    thermal_model_client,
    tuesday_slot_schedule,
    weekly_schedule,
)

# A Tuesday, ten minutes into the 05:00 hour.
NOW = datetime(2026, 11, 17, 5, 10, tzinfo=UTC)
# The fixtures' own time: Tuesday afternoon.
NOW_PLAN = room_forecast_fixtures.NOW


def seed_inputs(client: MariaDBClient, schedule: dict) -> None:
    seed_reading(client, NOW - timedelta(minutes=5), room=16.0)
    seed_schedule(client, schedule)
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 5), last=datetime(2026, 11, 19, 0)
    )


def test_the_days_slots_run_from_each_start_to_the_next_and_the_last_to_midnight(
    client: MariaDBClient,
) -> None:
    # Given a schedule with Tuesday slots at 00:00 (7 C), 05:40 (19 C), 06:25 (7 C)
    seed_inputs(client, tuesday_slot_schedule())

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then today's current slots run 00:00-05:40 at 7, 05:40-06:25 at 19 and
    # 06:25-00:00 at 7
    today = [row for row in slot_rows(client, "current") if row.day.day == 17]
    assert [(row.starts_at, row.ends_at, row.target) for row in today] == [
        (datetime(2026, 11, 17, 0, 0), datetime(2026, 11, 17, 5, 40), 7.0),
        (datetime(2026, 11, 17, 5, 40), datetime(2026, 11, 17, 6, 25), 19.0),
        (datetime(2026, 11, 17, 6, 25), datetime(2026, 11, 18, 0, 0), 7.0),
    ]


def test_a_first_slot_after_midnight_is_preceded_by_the_previous_days_last_target(
    client: MariaDBClient,
) -> None:
    # Given Tuesday's first slot starts at 05:40 and Monday ends at 9 C
    schedule = weekly_schedule()
    schedule["monday"] = [{"start": 0, "value": {"target": 9.0}}]
    schedule["tuesday"] = [
        {"start": SLOT_START, "value": {"target": 19.0}},
        {"start": SLOT_END, "value": {"target": 7.0}},
    ]
    seed_inputs(client, schedule)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then Monday's 9 C runs from 00:00 to 05:40
    today = [row for row in slot_rows(client, "current") if row.day.day == 17]
    first = today[0]
    assert (first.starts_at, first.ends_at, first.target) == (
        datetime(2026, 11, 17, 0, 0),
        datetime(2026, 11, 17, 5, 40),
        9.0,
    )


def test_consecutive_slots_with_the_same_target_are_stored_as_one(
    client: MariaDBClient,
) -> None:
    # Given Tuesday's slots at 00:00 and 05:40 both at 7 C
    schedule = weekly_schedule()
    schedule["tuesday"] = [
        {"start": 0, "value": {"target": 7.0}},
        {"start": SLOT_START, "value": {"target": 7.0}},
    ]
    seed_inputs(client, schedule)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then today is one slot from 00:00 to 00:00
    today = [row for row in slot_rows(client, "current") if row.day.day == 17]
    assert [(row.starts_at, row.ends_at, row.target) for row in today] == [
        (datetime(2026, 11, 17, 0, 0), datetime(2026, 11, 18, 0, 0), 7.0)
    ]


def test_an_unreadable_schedule_removes_the_earlier_slots(
    client: MariaDBClient,
) -> None:
    # Given slots stored by an earlier run
    seed_inputs(client, tuesday_slot_schedule())
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)
    assert slot_rows(client, "current")

    # When the weekly schedule becomes unreadable and the job runs
    seed_schedule(client, {"tuesday": "unreadable"})
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then no current slots remain
    assert not slot_rows(client, "current")


def test_the_slots_are_stored_when_the_mode_is_off(client: MariaDBClient) -> None:
    # Given Hive's mode is OFF
    seed_inputs(client, tuesday_slot_schedule())
    seed_reading(client, NOW - timedelta(minutes=1), room=16.0, mode="OFF")
    seed_gas_rate(client, unit_rate_p=6.0)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW)

    # Then today's slots are still stored, and no hour costs anything
    assert len([row for row in slot_rows(client, "current") if row.day.day == 17]) == 3
    assert all(row.cost_gbp == 0 for row in live_rows(client, "current"))


def test_the_stored_times_are_the_correct_instants_on_a_clock_change_day(
    client: MariaDBClient,
) -> None:
    # Given 29 March 2026, a Sunday when London moves to BST at 01:00 UTC
    now = datetime(2026, 3, 29, 5, 10, tzinfo=UTC)
    schedule = weekly_schedule()
    schedule["sunday"] = [
        {"start": 0, "value": {"target": 7.0}},
        {"start": SLOT_START, "value": {"target": 19.0}},
        {"start": SLOT_END, "value": {"target": 7.0}},
    ]
    seed_reading(client, now - timedelta(minutes=5), room=16.0)
    seed_schedule(client, schedule)
    seed_weather_forecast(
        client, first=datetime(2026, 3, 29, 5), last=datetime(2026, 3, 31, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(now)

    # Then 05:40 BST is 04:40 UTC, and the day runs from 00:00 GMT to 23:00 UTC
    sunday = [row for row in slot_rows(client, "current") if row.day.day == 29]
    assert [(row.starts_at, row.ends_at) for row in sunday] == [
        (datetime(2026, 3, 29, 0, 0), datetime(2026, 3, 29, 4, 40)),
        (datetime(2026, 3, 29, 4, 40), datetime(2026, 3, 29, 5, 25)),
        (datetime(2026, 3, 29, 5, 25), datetime(2026, 3, 29, 23, 0)),
    ]


def test_a_slot_boundary_in_the_spring_forward_gap_never_ends_before_it_starts(
    client: MariaDBClient,
) -> None:
    # Given 29 March 2026, when local 01:00-02:00 does not exist, and a slot from
    # 01:45 to 02:15
    now = datetime(2026, 3, 29, 5, 10, tzinfo=UTC)
    schedule = weekly_schedule()
    schedule["sunday"] = [
        {"start": 0, "value": {"target": 7.0}},
        {"start": 105, "value": {"target": 19.0}},
        {"start": 135, "value": {"target": 7.0}},
    ]
    seed_reading(client, now - timedelta(minutes=5), room=16.0)
    seed_schedule(client, schedule)
    seed_weather_forecast(
        client, first=datetime(2026, 3, 29, 5), last=datetime(2026, 3, 31, 0)
    )

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(now)

    # Then no stored slot ends before it starts or has no length, and the
    # positions run from 0
    sunday = [row for row in slot_rows(client, "current") if row.day.day == 29]
    assert sunday
    assert all(row.ends_at > row.starts_at for row in sunday)
    assert [row.position for row in sunday] == list(range(len(sunday)))
    # and the slots do not overlap
    assert all(a.ends_at <= b.starts_at for a, b in pairwise(sunday))


def test_the_plan_slot_ends_at_the_correct_instant_on_a_clock_change_day(
    client: MariaDBClient,
) -> None:
    # Given 29 March 2026, when London moves to BST at 01:00 UTC, a cold room and
    # a comfort temperature
    now = datetime(2026, 3, 29, 5, 10, tzinfo=UTC)
    seed_reading(client, now - timedelta(minutes=5), room=14.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 3, 29, 5), last=datetime(2026, 3, 31, 0)
    )
    seed_comfort(client, now.date(), 19.3)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(now)

    # Then the window closes at midnight BST, which is 23:00 UTC
    sunday = [row for row in slot_rows(client, "plan") if row.day.day == 29]
    assert [row.ends_at for row in sunday] == [datetime(2026, 3, 29, 23, 0)]


def test_the_plan_slot_runs_from_its_pre_heat_start_to_midnight_at_a_rounded_target(
    client: MariaDBClient,
) -> None:
    # Given a comfort temperature of 19.3 C, a cold room and the default window
    # (06:30 to midnight on weekdays)
    seed_plan_inputs(client, room=14.0)
    seed_comfort(client, NOW_PLAN.date(), 19.3)

    # When the job runs
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW_PLAN)

    # Then tomorrow's plan slot starts before 06:30 (the pre-heat), ends at
    # midnight and holds 19.5 C, the pre-change comfort temperature rounded up to a
    # half
    tomorrow = [row for row in slot_rows(client, "plan") if row.day.day == 18]
    assert len(tomorrow) == 1
    slot = tomorrow[0]
    assert slot.starts_at < datetime(2026, 11, 18, 6, 30)
    assert slot.starts_at > datetime(2026, 11, 18, 0, 0)
    assert slot.ends_at == datetime(2026, 11, 19, 0, 0)
    assert slot.target == 19.5


def store_plan_slot(
    client: MariaDBClient, day: int, starts_at: datetime, target: float
) -> None:
    client.replace_room_forecast_slots(
        "plan",
        [
            RoomForecastSlotRecord(
                datetime(2026, 11, day).date(),
                "plan",
                0,
                starts_at,
                datetime(2026, 11, day, 22, 0),
                target,
                NOW_PLAN.replace(tzinfo=None),
            )
        ],
    )


def test_a_plan_start_that_has_passed_is_kept_while_the_end_and_target_update(
    client: MariaDBClient,
) -> None:
    # Given today's stored plan slot started at 05:50, before now (14:10)
    seed_plan_inputs(client, room=14.0)
    seed_comfort(client, NOW_PLAN.date(), 19.3)
    store_plan_slot(client, 17, datetime(2026, 11, 17, 5, 50), 18.0)

    # When a later run recomputes
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW_PLAN)

    # Then the start stays and the end and target are the new ones
    today = [row for row in slot_rows(client, "plan") if row.day.day == 17]
    assert [(row.starts_at, row.ends_at, row.target) for row in today] == [
        (datetime(2026, 11, 17, 5, 50), datetime(2026, 11, 18, 0, 0), 19.5)
    ]


def test_a_plan_start_that_is_exactly_now_is_kept(client: MariaDBClient) -> None:
    # Given today's stored plan slot starts at the minute the job runs
    seed_plan_inputs(client, room=14.0)
    seed_comfort(client, NOW_PLAN.date(), 19.3)
    store_plan_slot(client, 17, NOW_PLAN.replace(tzinfo=None), 18.0)

    # When the job runs at that minute
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW_PLAN)

    # Then the start is still that minute
    today = [row for row in slot_rows(client, "plan") if row.day.day == 17]
    assert [row.starts_at for row in today] == [NOW_PLAN.replace(tzinfo=None)]


def test_a_plan_start_still_ahead_is_replaced_by_the_new_one(
    client: MariaDBClient,
) -> None:
    # Given tomorrow's stored plan slot starts at 05:00, after now
    seed_plan_inputs(client, room=14.0)
    seed_comfort(client, NOW_PLAN.date(), 19.3)
    store_plan_slot(client, 18, datetime(2026, 11, 18, 5, 0), 19.5)

    # When a later run recomputes
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW_PLAN)

    # Then the start is the newly calculated one
    tomorrow = [row for row in slot_rows(client, "plan") if row.day.day == 18]
    assert len(tomorrow) == 1
    assert tomorrow[0].starts_at != datetime(2026, 11, 18, 5, 0)


def test_no_comfort_temperature_removes_the_earlier_plan_slots(
    client: MariaDBClient,
) -> None:
    # Given plan slots stored by an earlier run
    seed_plan_inputs(client, room=14.0)
    seed_comfort(client, NOW_PLAN.date(), 19.3)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW_PLAN)
    assert slot_rows(client, "plan")

    # When the comfort temperature is gone and the job runs
    seed_comfort(client, NOW_PLAN.date(), None)
    RoomForecastJob(client, REGION, PINNED_WINDOWS).refresh(NOW_PLAN)

    # Then no plan slots remain
    assert not slot_rows(client, "plan")


def test_each_plan_slot_is_stored_under_the_day_its_window_opens(
    client: MariaDBClient,
) -> None:
    # Given Friday's window runs 06:00 to 03:00 (Saturday) and Saturday's 01:00 to
    # 22:00, so both close on Saturday
    now = datetime(2026, 11, 20, 14, 10, tzinfo=UTC)
    windows = ComfortWindows(
        weekday=ComfortWindow(start=time(6, 0), end=time(3, 0)),
        weekend=ComfortWindow(start=time(1, 0), end=time(22, 0)),
    )
    seed_reading(client, now - timedelta(minutes=5), room=14.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 20, 14), last=datetime(2026, 11, 23, 0)
    )
    seed_comfort(client, now.date(), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION, comfort_windows=windows).refresh(now)

    # Then there is one plan slot per opening day, Friday's ending Saturday 03:00
    rows = slot_rows(client, "plan")
    assert [(row.day.day, row.position, row.ends_at) for row in rows] == [
        (20, 0, datetime(2026, 11, 21, 3, 0)),
        (21, 0, datetime(2026, 11, 21, 22, 0)),
    ]


def test_two_windows_closing_at_the_same_instant_each_get_a_plan_slot(
    client: MariaDBClient,
) -> None:
    # Given Friday's window runs 22:00 to 06:00 (Saturday) and Saturday's 00:00 to
    # 06:00, so both close Saturday 06:00
    now = datetime(2026, 11, 20, 20, 10, tzinfo=UTC)
    windows = ComfortWindows(
        weekday=ComfortWindow(start=time(22, 0), end=time(6, 0)),
        weekend=ComfortWindow(start=time(0, 0), end=time(6, 0)),
    )
    seed_reading(client, now - timedelta(minutes=5), room=14.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 20, 20), last=datetime(2026, 11, 23, 0)
    )
    seed_comfort(client, now.date(), 19.0)

    # When the job runs
    RoomForecastJob(client, REGION, comfort_windows=windows).refresh(now)

    # Then one plan slot is stored under each opening day
    rows = slot_rows(client, "plan")
    assert [(row.day.day, row.position) for row in rows] == [(20, 0), (21, 0)]
    assert rows[0].ends_at == datetime(2026, 11, 21, 6, 0)
    assert rows[1].ends_at == datetime(2026, 11, 21, 6, 0)


def test_a_plan_slot_is_kept_after_its_window_has_closed_until_the_day_ends(
    client: MariaDBClient,
) -> None:
    # Given today's stored plan slot ends 22:00 and the window is 06:30 to 22:00
    now = datetime(2026, 11, 17, 22, 30, tzinfo=UTC)
    windows = ComfortWindows(
        weekday=ComfortWindow(start=time(6, 30), end=time(22, 0)),
        weekend=ComfortWindow(start=time(6, 30), end=time(22, 0)),
    )
    seed_reading(client, now - timedelta(minutes=5), room=14.0)
    seed_schedule(client, weekly_schedule())
    seed_weather_forecast(
        client, first=datetime(2026, 11, 17, 22), last=datetime(2026, 11, 20, 0)
    )
    seed_comfort(client, now.date(), 19.3)
    store_plan_slot(client, 17, datetime(2026, 11, 17, 5, 50), 19.5)

    # When the job runs after the window has closed
    RoomForecastJob(client, REGION, comfort_windows=windows).refresh(now)

    # Then today's slot is still stored with the same start and end
    today = [row for row in slot_rows(client, "plan") if row.day.day == 17]
    assert [(row.starts_at, row.ends_at) for row in today] == [
        (datetime(2026, 11, 17, 5, 50), datetime(2026, 11, 17, 22, 0))
    ]

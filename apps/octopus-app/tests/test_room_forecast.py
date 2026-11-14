"""The room forecast's pure maths: the schedule read defensively, the target in
force, and one hour held at a target."""

from datetime import date, datetime, time, timedelta

import pytest

from octopus_app.common.config import ComfortWindows
from octopus_app.data.room_forecast import (
    ForecastHour,
    Start,
    comfort_plan,
    comfort_window,
    hold_target,
    parse_schedule,
    round_up_to_half,
    target_at,
)
from octopus_app.data.thermal import ThermalNumbers

NUMBERS = ThermalNumbers(
    loss_per_degree=0.02,
    heating_gain=1.3,
    carried_gain=0.3,
    sun_gain=0.4,
    constant_gain=0.08,
)
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _raw_schedule(**slots_by_day: list[tuple[int, float]]) -> dict[str, list[dict]]:
    """Hive's payload shape; days not given have one 7 C slot from midnight."""
    return {
        day: [
            {"start": start, "value": {"target": target}}
            for start, target in slots_by_day.get(day, [(0, 7.0)])
        ]
        for day in DAYS
    }


def test_the_heating_fraction_is_the_least_that_holds_the_target() -> None:
    # Passive change at 18 C with 10 C outside and nothing carried: -0.08 C.
    # To reach 19 C needs (19 - 18 + 0.08) / 1.3 of the hour's heating.
    fraction, end_room = hold_target(NUMBERS, 18.0, 19.0, 10.0, 0.0, 0.0)

    assert fraction == pytest.approx(1.08 / 1.3)
    assert end_room == pytest.approx(19.0)


def test_the_heating_fraction_is_clamped_to_the_hour() -> None:
    # A target the room cannot reach in an hour heats flat out and falls short
    full, short_room = hold_target(NUMBERS, 18.0, 25.0, 10.0, 0.0, 0.0)
    # A target already exceeded needs no heating, and the room drifts down
    none, drifted_room = hold_target(NUMBERS, 18.0, 10.0, 10.0, 0.0, 0.0)

    assert full == 1.0
    assert short_room == pytest.approx(18.0 - 0.08 + 1.3)
    assert none == 0.0
    assert drifted_room == pytest.approx(17.92)


def test_the_target_is_the_slot_in_force_at_the_moment() -> None:
    schedule = parse_schedule(_raw_schedule(tuesday=[(0, 16.0), (390, 19.0)]))
    assert schedule is not None

    # Tuesday 2026-11-17: 06:30 is minute 390
    assert target_at(schedule, datetime(2026, 11, 17, 6, 29)) == 16.0
    assert target_at(schedule, datetime(2026, 11, 17, 6, 30)) == 19.0


def test_a_day_with_no_slot_yet_keeps_the_previous_days_last_target() -> None:
    schedule = parse_schedule(
        _raw_schedule(monday=[(0, 7.0), (1260, 12.0)], tuesday=[(390, 19.0)])
    )
    assert schedule is not None

    # Tuesday 05:00, before its first slot: Monday's last slot still holds
    assert target_at(schedule, datetime(2026, 11, 17, 5, 0)) == 12.0


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {"monday": "no slots"},
        {day: [] for day in DAYS},
        {**_raw_schedule(), "friday": [{"start": "0", "value": {"target": 7.0}}]},
        {**_raw_schedule(), "friday": [{"start": 0, "value": {"target": "warm"}}]},
        {**_raw_schedule(), "friday": [{"start": 0, "value": 7.0}]},
        {key: value for key, value in _raw_schedule().items() if key != "sunday"},
    ],
    ids=[
        "none",
        "list",
        "day without slots",
        "empty days",
        "text start",
        "text target",
        "value not a mapping",
        "a day missing",
    ],
)
def test_a_schedule_that_is_not_day_names_with_slots_is_not_read(raw: object) -> None:
    assert parse_schedule(raw) is None


TUESDAY = date(2026, 11, 17)
SATURDAY = date(2026, 11, 21)


def _hours(first: datetime, count: int) -> list[ForecastHour]:
    """Hours at 10 C outside with no sunshine."""
    return [
        ForecastHour(first + timedelta(hours=index), 10.0, 0.0)
        for index in range(count)
    ]


@pytest.mark.parametrize(
    ("comfort", "plan_target"),
    [(19.3, 19.5), (19.5, 19.5), (19.0, 19.0), (19.51, 20.0), (19.0000000001, 19.0)],
)
def test_the_plan_target_rounds_up_to_the_next_half_degree(
    comfort: float, plan_target: float
) -> None:
    assert round_up_to_half(comfort) == plan_target


def test_weekdays_start_at_0630_and_weekends_at_0745_both_ending_at_midnight() -> None:
    windows = ComfortWindows()

    assert comfort_window(windows, TUESDAY) == (
        datetime(2026, 11, 17, 6, 30),
        datetime(2026, 11, 18, 0, 0),
    )
    assert comfort_window(windows, SATURDAY) == (
        datetime(2026, 11, 21, 7, 45),
        datetime(2026, 11, 22, 0, 0),
    )
    assert comfort_window(windows, date(2026, 11, 22))[0].time() == time(7, 45)
    assert comfort_window(windows, date(2026, 11, 23))[0].time() == time(6, 30)


def test_the_preheat_is_the_shortest_run_that_reaches_the_target_at_the_start() -> None:
    # Room 17 C at 05:00, plan target 18.5 C, window start 06:30. Heating a
    # fraction a of the 05:00 hour and all of the 06:00 hour leaves, at 06:30
    # (halfway through the 06:00 hour), 17.5606 + 1.437 a C, which is 18.5 C for
    # a = 0.9394 / 1.437 = 0.653723.
    plan = comfort_plan(
        NUMBERS,
        Start(room=17.0, previous_fraction=0.0),
        _hours(datetime(2026, 11, 17, 5), 4),
        18.5,
        ComfortWindows(),
    )

    assert [step.fraction for step in plan[:2]] == pytest.approx(
        [0.653723, 1.0], abs=1e-5
    )
    # the room at 06:30, halfway through the 06:00 hour, is just the target
    assert (plan[1].room + plan[2].room) / 2 == pytest.approx(18.5, abs=1e-4)
    # the hour 06:30 falls in is the pre-heat's last, so 07:00 is the first held:
    # the room is already above 18.5 C, so it does not heat
    assert plan[2].fraction == 0.0


def test_a_room_already_warm_at_the_start_needs_no_preheat() -> None:
    plan = comfort_plan(
        NUMBERS,
        Start(room=20.0, previous_fraction=0.0),
        _hours(datetime(2026, 11, 17, 3), 6),
        18.5,
        ComfortWindows(),
    )

    assert [step.fraction for step in plan] == [0.0] * 6


def test_a_target_out_of_reach_heats_every_hour_available() -> None:
    plan = comfort_plan(
        NUMBERS,
        Start(room=10.0, previous_fraction=0.0),
        _hours(datetime(2026, 11, 17, 4), 4),
        22.0,
        ComfortWindows(),
    )

    # hours 04:00 to 06:00 lead to 06:30 and all heat
    assert [step.fraction for step in plan[:3]] == [1.0, 1.0, 1.0]


def test_no_preheat_when_the_forecast_starts_inside_the_window() -> None:
    # 10:00 on a Tuesday, room 18 C, target 19 C: the first hour just holds it
    plan = comfort_plan(
        NUMBERS,
        Start(room=18.0, previous_fraction=0.0),
        _hours(datetime(2026, 11, 17, 10), 3),
        19.0,
        ComfortWindows(),
    )

    assert plan[0].room == 18.0
    assert plan[0].fraction == pytest.approx(1.08 / 1.3)


def test_the_weekend_window_starts_later_than_the_weekday_one() -> None:
    # The same cold room at 05:00 and the same target on a Tuesday and a Saturday
    cold, target = Start(17.0, 0.0), 18.5
    tuesday = comfort_plan(
        NUMBERS, cold, _hours(datetime(2026, 11, 17, 5), 5), target, ComfortWindows()
    )
    saturday = comfort_plan(
        NUMBERS, cold, _hours(datetime(2026, 11, 21, 5), 5), target, ComfortWindows()
    )

    # 07:00 is held (not heating) before a 06:30 start, but is still pre-heat, the
    # hour 07:45 falls in, at the weekend
    assert tuesday[2].fraction == 0.0
    assert saturday[2].fraction > 0.0

"""The room forecast's pure maths: the schedule read defensively, the target in
force, and one hour held at a target."""

from datetime import date, datetime, time, timedelta

import pytest

from octopus_app.common.config import ComfortWindow, ComfortWindows
from octopus_app.data.room_forecast import (
    ForecastHour,
    Part,
    PlannedHour,
    Start,
    comfort_plan,
    comfort_window,
    hold_forecast,
    parse_schedule,
    round_up_to_half,
    schedule_parts,
    step_hour,
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


COLD_HOUR = ForecastHour(datetime(2026, 11, 17, 5), outdoor=10.0, radiation_kw=0.0)


def test_the_heating_fraction_is_the_least_that_holds_the_target() -> None:
    # Passive change at 18 C with 10 C outside and nothing carried: -0.08 C.
    # To reach 19 C needs (19 - 18 + 0.08) / 1.3 of the hour's heating.
    fraction, end_room = step_hour(NUMBERS, 18.0, COLD_HOUR, 0.0, [Part(1.0, 19.0)])

    assert fraction == pytest.approx(1.08 / 1.3)
    assert end_room == pytest.approx(19.0)


def test_the_heating_fraction_is_clamped_to_the_hour() -> None:
    # A target the room cannot reach in an hour heats flat out and falls short
    full, short_room = step_hour(NUMBERS, 18.0, COLD_HOUR, 0.0, [Part(1.0, 25.0)])
    # A target already exceeded needs no heating, and the room drifts down
    none, drifted_room = step_hour(NUMBERS, 18.0, COLD_HOUR, 0.0, [Part(1.0, 10.0)])

    assert full == 1.0
    assert short_room == pytest.approx(18.0 - 0.08 + 1.3)
    assert none == 0.0
    assert drifted_room == pytest.approx(17.92)


def test_each_part_heats_for_at_most_its_own_share_of_the_hour() -> None:
    # Room 16 C, 10 C outside, nothing carried: the hour's drift is
    # 0.02 * (10 - 16) + 0.08 = -0.04 C, spread evenly. The first two thirds
    # have a 7 C target and drift -0.04 * 2/3 to 15.97333 C. The last third
    # wants 19 C, far out of reach, so heats its whole third and gains
    # 1.3 / 3 - 0.04 / 3 = 0.42 C: 16.39333 C.
    parts = [Part(2 / 3, 7.0), Part(1 / 3, 19.0)]

    fraction, end_room = step_hour(NUMBERS, 16.0, COLD_HOUR, 0.0, parts)

    assert fraction == pytest.approx(1 / 3)
    assert end_room == pytest.approx(16.39333, abs=1e-5)


def test_an_hour_is_split_wherever_a_slot_starts_inside_it() -> None:
    # Tuesday 2026-11-17: 7 C from midnight, 19 C from 05:40 (minute 340), 7 C
    # from 06:25 (minute 385)
    schedule = parse_schedule(
        _raw_schedule(tuesday=[(0, 7.0), (340, 19.0), (385, 7.0)])
    )
    assert schedule is not None

    # 05:00: 40 minutes at 7 C, then 20 at 19 C
    assert schedule_parts(schedule, datetime(2026, 11, 17, 5)) == [
        Part(40 / 60, 7.0),
        Part(20 / 60, 19.0),
    ]
    # 06:00: 25 minutes at 19 C, then 35 at 7 C
    assert schedule_parts(schedule, datetime(2026, 11, 17, 6)) == [
        Part(25 / 60, 19.0),
        Part(35 / 60, 7.0),
    ]


def test_an_hour_with_no_slot_starting_inside_it_is_one_part() -> None:
    schedule = parse_schedule(
        _raw_schedule(tuesday=[(0, 7.0), (340, 19.0), (385, 7.0)])
    )
    assert schedule is not None

    # 04:00 sits in the 7 C slot; 07:00 is after the last slot
    assert schedule_parts(schedule, datetime(2026, 11, 17, 4)) == [Part(1.0, 7.0)]
    assert schedule_parts(schedule, datetime(2026, 11, 17, 7)) == [Part(1.0, 7.0)]


def test_a_slot_starting_on_the_hour_does_not_split_it() -> None:
    schedule = parse_schedule(_raw_schedule(tuesday=[(0, 7.0), (300, 19.0)]))
    assert schedule is not None

    assert schedule_parts(schedule, datetime(2026, 11, 17, 5)) == [Part(1.0, 19.0)]


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


def test_the_room_is_at_the_target_at_the_opening_minute_with_no_preheat_after_it() -> (
    None
):
    # Room 17 C at 05:00, plan target 18.5 C, window opening 06:30, 10 C outside.
    # The 05:00 hour drifts 0.02 * (10 - 17) + 0.08 = -0.06 C. Heating the last
    # share a of it and the first half of the 06:00 hour, the room at 06:30 is
    # 17 - 0.06 + 1.3 a, then half the 06:00 drift, 0.5 * (-0.0588 + 0.274 a),
    # and 0.65 C of heating: 17.5606 + 1.437 a, which is 18.5 C for
    # a = 0.9394 / 1.437 = 0.653723.
    plan = comfort_plan(
        NUMBERS,
        Start(room=17.0, previous_fraction=0.0),
        _hours(datetime(2026, 11, 17, 5), 4),
        18.5,
        ComfortWindows(),
    )

    # The 06:00 hour heats only its first half: no pre-heat minute after 06:30,
    # and the held second half needs none
    assert [step.fraction for step in plan[:3]] == pytest.approx(
        [0.653723, 0.5, 0.0], abs=1e-5
    )
    # The second half drifts 0.5 * (-0.0588 + 0.274 * 0.653723) = +0.060163 C from
    # 18.5 C at 06:30, so the room at 07:00 is 18.560163 C only if it was at 18.5
    assert plan[2].room == pytest.approx(18.560163, abs=1e-5)


def test_the_preheat_ends_at_the_opening_minute_and_the_target_is_held_from_it() -> (
    None
):
    # Given a room at 18 C at 06:00, a plan target of 18.5 C and a window opening
    # at 06:30, with 10 C outside: the hour drifts 0.02 * (10 - 18) + 0.08 = -0.08 C
    plan = comfort_plan(
        NUMBERS,
        Start(room=18.0, previous_fraction=0.0),
        _hours(datetime(2026, 11, 17, 6), 3),
        18.5,
        ComfortWindows(),
    )

    # Then the pre-heat is the shortest run ending at 06:30 that has the room at
    # 18.5 C there: 18 - 0.04 + 1.3 * lead = 18.5, so the lead is 0.54 / 1.3 =
    # 0.415385 of the hour (about 25 minutes), none of it after 06:30. From 06:30 the
    # target is held: 0.04 / 1.3 = 0.030769 of the hour keeps 18.5 C to 07:00.
    assert plan[0].fraction == pytest.approx(0.415385 + 0.030769, abs=1e-5)
    assert plan[1].room == pytest.approx(18.5, abs=1e-4)


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


def test_the_first_hour_steps_only_the_part_after_the_reading_and_carries_what_was_seen() -> (
    None
):
    # Room 18 C at 06:30, with a quarter of the hour already heated before it and
    # 19 C wanted all day; 10 C outside, nothing carried from 05:00. The hour's
    # drift is 0.02 * (10 - 18) + 0.08 = -0.08 C, and the half hour left needs
    # (19 - 18 + 0.5 * 0.08) / 1.3 = 0.8 of an hour: more than the 0.5 there is.
    plan = hold_forecast(
        NUMBERS,
        Start(18.0, 0.0, at=datetime(2026, 11, 17, 6, 30), seen_fraction=0.25),
        _hours(datetime(2026, 11, 17, 6), 3),
        lambda _: [Part(1.0, 19.0)],
    )

    assert plan[0] == PlannedHour(room=18.0, fraction=0.5)
    # The room at 07:00 is 18 - 0.04 + 0.65 = 18.61 C. The 07:00 hour carries the
    # quarter seen and the half to come, 0.75: its drift is
    # 0.02 * (10 - 18.61) + 0.08 + 0.3 * 0.75 = 0.1328 C, so holding 19 C needs
    # (19 - 18.61 - 0.1328) / 1.3 = 0.19785 of the hour
    assert plan[1].room == pytest.approx(18.61)
    assert plan[1].fraction == pytest.approx(0.19785, abs=1e-5)


def test_a_preheat_that_began_before_the_next_hour_is_searched_from_the_reading() -> (
    None
):
    # Room 18.3 C at 05:50, plan target 19 C, window opening 06:30, 10 C outside.
    # Unheated the room is 18.3 - 1/6 * 0.086 = 18.28567 C at 06:00. With the
    # pre-heat starting x of an hour before 06:00 (0.3 x carried into the 06:00
    # hour), the room at 06:30 is
    # 18.28567 + 1.3 x + 0.5 * (-0.08571 + 0.274 x) + 0.65 = 19: x = 0.074594.
    plan = comfort_plan(
        NUMBERS,
        Start(18.3, 0.0, at=datetime(2026, 11, 17, 5, 50)),
        _hours(datetime(2026, 11, 17, 5), 3),
        19.0,
        ComfortWindows(),
    )

    assert plan[0].room == 18.3
    assert plan[0].fraction == pytest.approx(0.074594, abs=1e-5)


def test_the_weekend_window_starts_later_than_the_weekday_one() -> None:
    # The same cold room at 05:00 and the same target on a Tuesday and a Saturday
    cold, target = Start(17.0, 0.0), 18.5
    tuesday = comfort_plan(
        NUMBERS, cold, _hours(datetime(2026, 11, 17, 5), 5), target, ComfortWindows()
    )
    saturday = comfort_plan(
        NUMBERS, cold, _hours(datetime(2026, 11, 21, 5), 5), target, ComfortWindows()
    )

    # Tuesday opens at 06:30: the 05:00 hour heats 0.653723 (see the test of the
    # opening minute) and 07:00 needs nothing. Saturday opens at 07:45: the 05:00
    # hour drifts to 16.94 C unheated, the 06:00 hour (drift -0.0588 C) heats its
    # last share b, and the 07:00 hour (drift -0.057624 + 0.274 b) heats its first
    # 45 minutes: 16.8812 + 1.3 b + 0.75 * (-0.057624 + 0.274 b) + 0.975 = 18.5
    # for b = 0.687018 / 1.5055 = 0.456339, and the held quarter hour needs none
    assert [step.fraction for step in tuesday[:3]] == pytest.approx(
        [0.653723, 0.5, 0.0], abs=1e-5
    )
    assert [step.fraction for step in saturday[:3]] == pytest.approx(
        [0.0, 0.456339, 0.75], abs=1e-5
    )


def test_an_overnight_window_is_still_held_when_the_forecast_starts_after_midnight() -> (
    None
):
    # Given a window from 22:00 to 06:00 and a forecast that starts at 02:00 on
    # Tuesday, inside the window that began on Monday at 22:00
    overnight = ComfortWindow(start=time(22, 0), end=time(6, 0))
    plan = comfort_plan(
        NUMBERS,
        Start(room=18.0, previous_fraction=0.0),
        _hours(datetime(2026, 11, 17, 2), 6),
        19.0,
        ComfortWindows(weekday=overnight, weekend=overnight),
    )

    # Then 02:00 holds 19 C from 18 C at once, (19 - 18 + 0.16 - 0.08) / 1.3 of
    # the hour, and the hours up to 06:00 are held, not pre-heat for tonight
    assert plan[0].fraction == pytest.approx(1.08 / 1.3)
    assert plan[1].room == pytest.approx(19.0)
    # the room is never below the target at the start of any hour up to 06:00
    assert all(step.room >= 19.0 - 1e-9 for step in plan[1:5])
    # and nothing heats once the window has closed at 06:00
    assert [step.fraction for step in plan[4:]] == [0.0, 0.0]


def test_the_target_is_held_to_the_closing_minute_and_nothing_after() -> None:
    # Given a window ending at 23:30 and a cold night (-10 C outside), room at the
    # target of 19 C at 21:00
    ending_early = ComfortWindow(start=time(6, 30), end=time(23, 30))
    cold = [
        ForecastHour(datetime(2026, 11, 17, 21) + timedelta(hours=index), -10.0, 0.0)
        for index in range(4)
    ]

    plan = comfort_plan(
        NUMBERS,
        Start(room=19.0, previous_fraction=0.0),
        cold,
        19.0,
        ComfortWindows(weekday=ending_early, weekend=ending_early),
    )

    # Then each hour holds 19 C: drift 0.02 * (-10 - 19) + 0.08 = -0.5 C, plus 0.3
    # of the hour before's heating. 21:00 heats 0.5 / 1.3 = 0.384615, 22:00 heats
    # (0.5 - 0.3 * 0.384615) / 1.3 = 0.295858, and 23:00, with drift
    # -0.5 + 0.3 * 0.295858 = -0.411243 C, holds only until 23:30: half of that,
    # 0.205621 C, is made up by 0.158170 of the hour
    assert [step.fraction for step in plan[:3]] == pytest.approx(
        [0.384615, 0.295858, 0.158170], abs=1e-5
    )
    # the other half hour drifts by another -0.205621 C and nothing heats it, nor
    # anything after midnight (the next window opens beyond the forecast)
    assert plan[3].room == pytest.approx(19.0 - 0.205621, abs=1e-5)
    assert plan[3].fraction == 0.0


def test_a_forecast_that_starts_in_the_hour_the_window_closes_in_stops_at_the_close() -> (
    None
):
    # Given a window ending at 23:30, and a forecast whose first hour is 23:00 in a
    # cold room below the target
    ending_early = ComfortWindow(start=time(6, 30), end=time(23, 30))
    plan = comfort_plan(
        NUMBERS,
        Start(room=17.0, previous_fraction=0.0),
        [ForecastHour(datetime(2026, 11, 17, 23), -10.0, 0.0)],
        19.0,
        ComfortWindows(weekday=ending_early, weekend=ending_early),
    )

    # Then the first half hour heats flat out, since the room is 2 C short and the
    # hour's drift is 0.02 * (-10 - 17) + 0.08 = -0.46 C, and nothing heats after
    # the close: the whole hour's heating is 0.5
    assert plan[0].fraction == 0.5

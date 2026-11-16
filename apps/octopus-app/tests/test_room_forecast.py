"""The room forecast's maths: the weekly schedule, and the thermal model stepped
forward in 2-minute steps (ADR-0035, ADR-0036). No database."""

from datetime import date, datetime, time, timedelta

import pytest

from octopus_app.common.config import ComfortWindow, ComfortWindows
from octopus_app.data.room_forecast import (
    NO_TARGET,
    STEP,
    ForecastHour,
    PlannedStep,
    RoomModel,
    Start,
    comfort_plan,
    comfort_window,
    hold_forecast,
    parse_schedule,
    plan_slots,
    round_up_to_half,
    schedule_target_at_step,
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
HOURLY_MODEL = RoomModel(NUMBERS)
# With nothing carried, the room changes only in the steps heat arrives in.
UNCARRIED = ThermalNumbers(0.02, 1.3, 0.0, 0.4, 0.08)
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
TUESDAY = date(2026, 11, 17)
SATURDAY = date(2026, 11, 21)
# At 18 C with 10 C outside and no sun, a 2-minute step with no heating changes
# the room by 2/60 * (0.02 * (10 - 18) + 0.08).
COLD_DRIFT = -0.08 * 2 / 60


def _raw_schedule(**slots_by_day: list[tuple[int, float]]) -> dict[str, list[dict]]:
    """Hive's payload shape; days not given have one 7 C slot from midnight."""
    return {
        day: [
            {"start": start, "value": {"target": target}}
            for start, target in slots_by_day.get(day, [(0, 7.0)])
        ]
        for day in DAYS
    }


def _hours(first: datetime, count: int, outdoor: float = 10.0) -> list[ForecastHour]:
    """Hours at a steady outdoor temperature with no sunshine."""
    return [
        ForecastHour(first + timedelta(hours=index), outdoor, outdoor, 0.0)
        for index in range(count)
    ]


def _steady(target: float):
    return lambda local: target


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


def test_a_slot_starting_on_an_odd_minute_takes_effect_from_the_step_before_it() -> (
    None
):
    # Given a slot at 19 C from 05:41, an odd minute
    schedule = parse_schedule(_raw_schedule(tuesday=[(0, 7.0), (341, 19.0)]))
    assert schedule is not None

    # Then the 05:40 step is at 19 C, and the step before it is not
    assert schedule_target_at_step(schedule, datetime(2026, 11, 17, 5, 38)) == 7.0
    assert schedule_target_at_step(schedule, datetime(2026, 11, 17, 5, 40)) == 19.0


def test_a_slot_starting_on_an_even_minute_takes_effect_from_that_step() -> None:
    schedule = parse_schedule(_raw_schedule(tuesday=[(0, 7.0), (340, 19.0)]))
    assert schedule is not None

    assert schedule_target_at_step(schedule, datetime(2026, 11, 17, 5, 38)) == 7.0
    assert schedule_target_at_step(schedule, datetime(2026, 11, 17, 5, 40)) == 19.0


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


@pytest.mark.parametrize(
    ("comfort", "plan_target"),
    [(19.3, 19.5), (19.5, 19.5), (19.0, 19.0), (19.51, 20.0), (19.0000000001, 19.0)],
)
def test_the_plan_target_rounds_up_to_the_next_half_degree(
    comfort: float, plan_target: float
) -> None:
    assert round_up_to_half(comfort) == plan_target


def test_weekdays_start_at_0630_and_weekends_at_0744_both_ending_at_midnight() -> None:
    windows = ComfortWindows()

    assert comfort_window(windows, TUESDAY) == (
        datetime(2026, 11, 17, 6, 30),
        datetime(2026, 11, 18, 0, 0),
    )
    assert comfort_window(windows, SATURDAY) == (
        datetime(2026, 11, 21, 7, 44),
        datetime(2026, 11, 22, 0, 0),
    )
    assert comfort_window(windows, date(2026, 11, 22))[0].time() == time(7, 44)
    assert comfort_window(windows, date(2026, 11, 23))[0].time() == time(6, 30)


def test_with_no_target_the_room_drifts_and_nothing_is_demanded() -> None:
    # Given a room of 18 C at 05:00 and no target
    start = Start(18.0, datetime(2026, 11, 17, 5, 0))

    steps = hold_forecast(
        HOURLY_MODEL, start, _hours(datetime(2026, 11, 17, 5), 1), _steady(NO_TARGET)
    )

    # Then it cools by 2/60 * (0.02 * (10 - 18) + 0.08) a step, over 30 steps
    assert len(steps) == 30
    assert [step.offset_minutes for step in steps[:3]] == [0, 2, 4]
    assert steps[0].room == 18.0
    assert steps[1].room == pytest.approx(18.0 + COLD_DRIFT)
    assert {step.demand_minutes for step in steps} == {0.0}


def test_a_step_heats_flat_out_when_the_room_is_far_below_the_target() -> None:
    start = Start(18.0, datetime(2026, 11, 17, 5, 0))

    steps = hold_forecast(
        HOURLY_MODEL, start, _hours(datetime(2026, 11, 17, 5), 1), _steady(19.0)
    )

    # Then the first step demands the whole 2 minutes, and the room gains
    # 1.3 * 2/60 less the drift
    assert steps[0].demand_minutes == 2.0
    assert steps[1].room == pytest.approx(18.0 + COLD_DRIFT + 1.3 * 2 / 60)


def test_a_step_demands_only_the_minutes_that_reach_the_target() -> None:
    # Given a target 0.01 C above the room
    start = Start(18.0, datetime(2026, 11, 17, 5, 0))

    steps = hold_forecast(
        HOURLY_MODEL, start, _hours(datetime(2026, 11, 17, 5), 1), _steady(18.01)
    )

    # Then the step demands (0.01 + 0.0026667) * 60 / 1.3 = 0.5846 minutes and the
    # room is at the target at the step's end
    assert steps[0].demand_minutes == pytest.approx(0.584615, abs=1e-5)
    assert steps[1].room == pytest.approx(18.01)


def test_the_forecast_starts_at_the_step_holding_the_reading() -> None:
    # Given a reading at 05:07, inside the 05:06 step
    start = Start(18.0, datetime(2026, 11, 17, 5, 7))

    steps = hold_forecast(
        HOURLY_MODEL, start, _hours(datetime(2026, 11, 17, 5), 1), _steady(NO_TARGET)
    )

    # Then the first step starts at 05:06 with the reading as its room
    assert steps[0].offset_minutes == 6
    assert steps[0].room == 18.0
    assert len(steps) == 27


def test_outdoor_temperature_runs_in_a_straight_line_between_the_hourly_stamps() -> (
    None
):
    # Given 10 C at 05:00 and 16 C at 06:00 and a room already at 13 C
    hours = [ForecastHour(datetime(2026, 11, 17, 5), 10.0, 16.0, 0.0)]
    numbers = ThermalNumbers(1.0, 1.3, 0.0, 0.0, 0.0)

    steps = hold_forecast(
        RoomModel(numbers),
        Start(13.0, datetime(2026, 11, 17, 5, 30)),
        hours,
        _steady(NO_TARGET),
    )

    # Then at 05:30 the outdoor is 13 C, so the room does not change in the step,
    # and at 05:32 it is 13.2 C
    assert steps[0].room == 13.0
    assert steps[1].room == pytest.approx(13.0)
    assert steps[2].room == pytest.approx(13.0 + 2 / 60 * (13.2 - 13.0))


def test_the_radiation_of_the_hour_warms_the_room() -> None:
    hours = [ForecastHour(datetime(2026, 11, 17, 12), 10.0, 10.0, 1.0)]

    steps = hold_forecast(
        HOURLY_MODEL,
        Start(18.0, datetime(2026, 11, 17, 12, 0)),
        hours,
        _steady(NO_TARGET),
    )

    # 2/60 * (0.02 * (10 - 18) + 0.4 * 1 + 0.08) = 0.0106667 C in the step
    assert steps[1].room == pytest.approx(18.0 + 2 / 60 * 0.32)


def test_heat_that_arrived_still_warms_the_room_for_the_model_step_after_it() -> None:
    # Given 2 minutes of heating in the first step only, on a model fitted hourly
    def target_of(local: datetime) -> float:
        return 19.0 if local == datetime(2026, 11, 17, 5, 0) else NO_TARGET

    steps = hold_forecast(
        HOURLY_MODEL,
        Start(18.0, datetime(2026, 11, 17, 5, 0)),
        _hours(datetime(2026, 11, 17, 5), 1),
        target_of,
    )

    # Then the next step also gains 0.3 * (2/60) * 2/60 from the heat before it
    after_heating = 18.0 + COLD_DRIFT + 1.3 * 2 / 60
    drift = 2 / 60 * (0.02 * (10 - after_heating) + 0.08)
    assert steps[2].room == pytest.approx(
        after_heating + drift + 0.3 * (2 / 60) * (2 / 60)
    )


def test_a_model_fitted_on_10_minutes_carries_a_fifth_of_the_heat_a_step_after() -> (
    None
):
    def target_of(local: datetime) -> float:
        return 19.0 if local == datetime(2026, 11, 17, 5, 0) else NO_TARGET

    steps = hold_forecast(
        RoomModel(NUMBERS, step_minutes=10),
        Start(18.0, datetime(2026, 11, 17, 5, 0)),
        _hours(datetime(2026, 11, 17, 5), 1),
        target_of,
    )

    # The 2 minutes carry for the 10 minutes after them: 0.3 * (2/10) * 2/60
    after_heating = 18.0 + COLD_DRIFT + 1.3 * 2 / 60
    drift = 2 / 60 * (0.02 * (10 - after_heating) + 0.08)
    assert steps[2].room == pytest.approx(
        after_heating + drift + 0.3 * (2 / 10) * (2 / 60)
    )
    # and the carried heat is gone 10 minutes later
    assert steps[7].room < steps[6].room


def test_heat_demanded_before_the_reading_arrives_after_it_when_there_is_a_delay() -> (
    None
):
    # Given a delay of 6 minutes (3 steps), and demand in each of the 3 steps
    # before the forecast starts, so it arrives in the first 3 steps
    model = RoomModel(UNCARRIED, step_minutes=2, delay_minutes=6)
    start = Start(18.0, datetime(2026, 11, 17, 5, 0), [2.0, 2.0, 2.0])

    steps = hold_forecast(
        model, start, _hours(datetime(2026, 11, 17, 5), 1), _steady(NO_TARGET)
    )

    # Then the room warms through the first three steps as recorded, then cools
    assert steps[1].room > steps[0].room
    assert steps[3].room > steps[2].room
    assert steps[4].room < steps[3].room
    assert {step.demand_minutes for step in steps} == {0.0}


def test_nothing_arrives_for_the_delay_after_a_target_starts_and_heat_keeps_arriving_after_it_ends() -> (
    None
):
    # Given a delay of 14 minutes (7 steps), a cold room and 19 C from 05:40 to 06:24
    model = RoomModel(UNCARRIED, step_minutes=2, delay_minutes=14)
    opens, closes = datetime(2026, 11, 17, 5, 40), datetime(2026, 11, 17, 6, 24)

    def target_of(local: datetime) -> float:
        return 19.0 if opens <= local < closes else NO_TARGET

    steps = hold_forecast(
        model,
        Start(16.0, datetime(2026, 11, 17, 5, 30)),
        _hours(datetime(2026, 11, 17, 5), 2),
        target_of,
    )

    # Then demand (gas) runs from 05:40 to 06:24, 22 steps of 2 minutes
    demanded = [step for step in steps if step.demand_minutes > 0]
    assert demanded[0].offset_minutes == 40
    assert demanded[-1].offset_minutes == 82  # 06:22
    assert sum(step.demand_minutes for step in steps) == pytest.approx(44.0)
    # the room follows its unheated course until 05:54 and climbs after it
    room = {step.offset_minutes: step.room for step in steps}
    assert room[54] < room[52] < room[50] < room[48]
    assert room[56] > room[54]
    assert room[58] > room[56]
    # and heat keeps arriving for the delay after 06:24: the last arrives at 06:36
    # (offset 96), so the room climbs to 06:38 and falls after it
    assert room[98] > room[96]
    assert room[100] < room[98]


def test_a_delay_of_zero_arrives_in_the_step_it_is_demanded() -> None:
    start = Start(18.0, datetime(2026, 11, 17, 5, 0))

    steps = hold_forecast(
        RoomModel(NUMBERS, delay_minutes=0),
        start,
        _hours(datetime(2026, 11, 17, 5), 1),
        _steady(19.0),
    )

    assert steps[1].room > steps[0].room


def _first_demand(steps: list[PlannedStep]) -> int:
    return min(step.offset_minutes for step in steps if step.demand_minutes > 0)


def _room_at(steps: list[PlannedStep], offset_minutes: int) -> float:
    return next(step.room for step in steps if step.offset_minutes == offset_minutes)


def test_the_room_is_at_the_target_at_the_opening_with_the_shortest_whole_step_lead() -> (
    None
):
    # Given a cold room at 04:00 and a weekday window opening at 06:30
    start = Start(17.0, datetime(2026, 11, 17, 4, 0))
    hours = _hours(datetime(2026, 11, 17, 4), 6)

    plan = comfort_plan(HOURLY_MODEL, start, hours, 18.5, ComfortWindows())

    # Then the room is at the target at 06:30 (150 minutes in)
    assert _room_at(plan, 150) >= 18.5 - 1e-9
    # and the pre-heat is the shortest in whole 2-minute steps: starting a step
    # later leaves the room short
    first = _first_demand(plan)
    assert first % 2 == 0
    opening = datetime(2026, 11, 17, 6, 30)
    later = hold_forecast(
        HOURLY_MODEL,
        start,
        hours,
        lambda local: (
            18.5
            if opening - (150 - first) * timedelta(minutes=1) + STEP <= local
            else NO_TARGET
        ),
    )
    assert _room_at(later, 150) < 18.5


def test_a_room_already_warm_enough_needs_no_preheat() -> None:
    # Given a room above the target that cannot fall below it by 06:30
    start = Start(19.9, datetime(2026, 11, 17, 6, 0))

    plan = comfort_plan(
        HOURLY_MODEL,
        start,
        _hours(datetime(2026, 11, 17, 6), 3),
        19.0,
        ComfortWindows(),
    )

    # Then nothing is demanded
    assert all(step.demand_minutes == 0 for step in plan)


def test_a_target_out_of_reach_heats_from_the_start_of_the_forecast() -> None:
    # Given a freezing night and a window 20 minutes away with a room 5 C short
    plan = comfort_plan(
        HOURLY_MODEL,
        Start(14.0, datetime(2026, 11, 17, 6, 10)),
        _hours(datetime(2026, 11, 17, 6), 2, outdoor=-10.0),
        19.0,
        ComfortWindows(),
    )

    # Then it heats from the first step
    assert _first_demand(plan) == 10


def test_a_warm_up_delay_moves_the_start_of_the_preheat_earlier_by_the_delay() -> None:
    start = Start(17.0, datetime(2026, 11, 17, 4, 0))
    hours = _hours(datetime(2026, 11, 17, 4), 6)

    without_delay = comfort_plan(HOURLY_MODEL, start, hours, 18.5, ComfortWindows())
    delayed_model = RoomModel(NUMBERS, step_minutes=60, delay_minutes=14)
    delayed = comfort_plan(delayed_model, start, hours, 18.5, ComfortWindows())

    # Then gas is demanded 14 minutes earlier and the room is still at the target
    # at the opening
    assert _first_demand(delayed) == _first_demand(without_delay) - 14
    assert _room_at(delayed, 150) == pytest.approx(_room_at(without_delay, 150))


def test_the_weekend_window_opens_at_0744_so_the_plan_starts_later_than_a_weekday() -> (
    None
):
    start = Start(17.0, datetime(2026, 11, 17, 4, 0))
    saturday_start = Start(17.0, datetime(2026, 11, 21, 4, 0))

    tuesday = comfort_plan(
        HOURLY_MODEL,
        start,
        _hours(datetime(2026, 11, 17, 4), 6),
        18.5,
        ComfortWindows(),
    )
    saturday = comfort_plan(
        HOURLY_MODEL,
        saturday_start,
        _hours(datetime(2026, 11, 21, 4), 6),
        18.5,
        ComfortWindows(),
    )

    # The Saturday window opens 74 minutes after Tuesday's
    assert _first_demand(saturday) > _first_demand(tuesday) + 60


def test_an_overnight_window_is_still_held_when_the_forecast_starts_after_midnight() -> (
    None
):
    # Given a window from 22:00 to 06:00 and a forecast that starts at 02:00 on
    # Tuesday, inside the window that began on Monday at 22:00
    overnight = ComfortWindow(start=time(22, 0), end=time(6, 0))

    plan = comfort_plan(
        HOURLY_MODEL,
        Start(18.0, datetime(2026, 11, 17, 2, 0)),
        _hours(datetime(2026, 11, 17, 2), 6),
        19.0,
        ComfortWindows(weekday=overnight, weekend=overnight),
    )

    # Then 02:00 holds 19 C from 18 C at once, and the room is never below the
    # target at the start of a step up to 06:00 once it has reached it
    assert plan[0].demand_minutes == 2.0
    held = [step for step in plan if 60 <= step.offset_minutes < 240]
    assert all(step.room >= 19.0 - 1e-6 for step in held)
    # and nothing is demanded once the window has closed at 06:00
    assert all(step.demand_minutes == 0 for step in plan if step.offset_minutes >= 240)


def test_the_target_is_held_to_the_closing_minute_and_nothing_after() -> None:
    # Given a window ending at 23:30 and a cold night, with the room at the target
    ending_early = ComfortWindow(start=time(6, 30), end=time(23, 30))

    plan = comfort_plan(
        HOURLY_MODEL,
        Start(19.0, datetime(2026, 11, 17, 21, 0)),
        _hours(datetime(2026, 11, 17, 21), 4, outdoor=-10.0),
        19.0,
        ComfortWindows(weekday=ending_early, weekend=ending_early),
    )

    # Then heat is demanded up to the close, and not after it
    assert sum(step.demand_minutes for step in plan if step.offset_minutes < 150) > 0
    assert all(step.demand_minutes == 0 for step in plan if step.offset_minutes >= 150)
    # and the room cools after the close
    assert _room_at(plan, 178) < _room_at(plan, 150)


def test_plan_slots_start_at_the_opening_less_the_lead_and_end_at_the_close() -> None:
    start = Start(17.0, datetime(2026, 11, 17, 4, 0))
    hours = _hours(datetime(2026, 11, 17, 4), 6)

    plan = comfort_plan(HOURLY_MODEL, start, hours, 18.5, ComfortWindows())
    slots = plan_slots(HOURLY_MODEL, start, hours, 18.5, ComfortWindows())

    day, slot = slots[0]
    assert day == TUESDAY
    assert slot.start == datetime(2026, 11, 17, 4) + timedelta(
        minutes=_first_demand(plan)
    )
    assert slot.end == datetime(2026, 11, 18, 0, 0)
    assert slot.target == 18.5


def test_on_a_spring_forward_day_the_preheat_can_start_in_the_hour_before_the_change() -> (
    None
):
    # Given the 23-hour day on which London's clocks go forward (29 March 2026):
    # the local hours are 00:00, then 02:00 onwards, and the weekend window opens at
    # 07:44. The room is far too cold for the target to be reached by then.
    hours = [ForecastHour(datetime(2026, 3, 29, 0), -10.0, -10.0, 0.0)] + [
        ForecastHour(datetime(2026, 3, 29, hour), -10.0, -10.0, 0.0)
        for hour in range(2, 8)
    ]

    plan = comfort_plan(
        HOURLY_MODEL,
        Start(10.0, datetime(2026, 3, 29, 0, 0)),
        hours,
        19.0,
        ComfortWindows(),
    )

    # Then it heats from the forecast's first step, not from 01:00 (which never
    # happens), so the hour before the change is not lost
    assert _first_demand(plan) == 0


def test_on_a_fall_back_day_an_out_of_reach_target_heats_from_the_first_step() -> None:
    # Given the 25-hour day on which clocks go back (25 October 2026): the local
    # hours are 00:00, 01:00, 01:00 again, then 02:00 onwards, and the room is far
    # too cold for the 07:44 weekend opening
    hours = [
        ForecastHour(datetime(2026, 10, 25, hour), -10.0, -10.0, 0.0)
        for hour in (0, 1, 1, 2, 3, 4, 5, 6, 7)
    ]

    plan = comfort_plan(
        HOURLY_MODEL,
        Start(5.0, datetime(2026, 10, 25, 0, 0)),
        hours,
        19.0,
        ComfortWindows(),
    )

    assert _first_demand(plan) == 0


def test_a_reachable_target_across_the_spring_forward_gap_is_at_the_opening() -> None:
    # Given the same 23-hour day with a room 4 C short, which the heating can make up
    # well before 07:44 (6 hours 44 minutes of steps, 7 hours 44 on the clock)
    hours = [ForecastHour(datetime(2026, 3, 29, 0), 0.0, 0.0, 0.0)] + [
        ForecastHour(datetime(2026, 3, 29, hour), 0.0, 0.0, 0.0) for hour in range(2, 8)
    ]

    plan = comfort_plan(
        HOURLY_MODEL,
        Start(15.0, datetime(2026, 3, 29, 0, 0)),
        hours,
        19.0,
        ComfortWindows(),
    )

    # Then the pre-heat starts part-way through the forecast, and the room is at the
    # target at 07:44, 404 minutes of steps in
    assert 0 < _first_demand(plan) < 404
    assert _room_at(plan, 404) >= 19.0 - 1e-9

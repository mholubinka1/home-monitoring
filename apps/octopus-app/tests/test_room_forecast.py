"""The room forecast's pure maths: the schedule read defensively, the target in
force, and one hour held at a target."""

from datetime import datetime

import pytest

from octopus_app.data.room_forecast import hold_target, parse_schedule, target_at
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

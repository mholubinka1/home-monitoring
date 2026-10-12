from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from common.local_day import (
    DEFAULT_TIMEZONE,
    expected_half_hour_count,
    get_local_timezone,
    hour_end_stamps,
    set_local_timezone,
    start_of_local_day,
    to_local_date,
)


def test_a_late_evening_bst_instant_before_utc_midnight_falls_on_the_next_local_day() -> (
    None
):
    # 2026-07-20 23:30 UTC is 2026-07-21 00:30 in Europe/London during BST --
    # the local day has already turned over even though the UTC day hasn't.
    instant = datetime(2026, 7, 20, 23, 30, tzinfo=UTC)

    assert to_local_date(instant) == date(2026, 7, 21)


def test_a_naive_instant_from_a_database_row_is_treated_as_utc() -> None:
    # consumption.period_from comes back from MariaDB/SQLite as a naive
    # datetime representing a UTC instant -- the same BST-crossing instant
    # as above, but without tzinfo attached.
    instant = datetime(2026, 7, 20, 23, 30)  # noqa: DTZ001 -- naive is the point

    assert to_local_date(instant) == date(2026, 7, 21)


def test_an_ordinary_day_has_forty_eight_expected_half_hour_slots() -> None:
    assert expected_half_hour_count(date(2026, 7, 20)) == 48


def test_the_uk_spring_forward_date_has_forty_six_expected_half_hour_slots() -> None:
    # 2026-03-29 is the last Sunday in March -- clocks go forward 01:00 to
    # 02:00 UK time, so the local day is only 23 hours long.
    assert expected_half_hour_count(date(2026, 3, 29)) == 46


def test_the_uk_fall_back_date_has_fifty_expected_half_hour_slots() -> None:
    # 2026-10-25 is the last Sunday in October -- clocks go back 02:00 to
    # 01:00 UK time, so the local day is 25 hours long.
    assert expected_half_hour_count(date(2026, 10, 25)) == 50


def test_days_either_side_of_a_clock_change_are_still_ordinary_forty_eight_slot_days() -> (
    None
):
    assert expected_half_hour_count(date(2026, 3, 28)) == 48
    assert expected_half_hour_count(date(2026, 3, 30)) == 48
    assert expected_half_hour_count(date(2026, 10, 24)) == 48
    assert expected_half_hour_count(date(2026, 10, 26)) == 48


def test_the_start_of_a_bst_local_day_is_one_hour_before_utc_midnight() -> None:
    # Local midnight on 2026-07-06 (BST, UTC+1) is UTC 2026-07-05 23:00.
    assert start_of_local_day(date(2026, 7, 6)) == datetime(
        2026, 7, 5, 23, 0, tzinfo=UTC
    )


def test_the_start_of_a_gmt_local_day_is_utc_midnight() -> None:
    # Outside BST (GMT, UTC+0), local midnight is UTC midnight.
    assert start_of_local_day(date(2026, 1, 6)) == datetime(
        2026, 1, 6, 0, 0, tzinfo=UTC
    )


def test_with_new_york_configured_a_local_day_starts_at_new_york_midnight() -> None:
    set_local_timezone("America/New_York")

    # Local midnight on 2026-07-06 (EDT, UTC-4) is 04:00 UTC the same date.
    assert start_of_local_day(date(2026, 7, 6)) == datetime(
        2026, 7, 6, 4, 0, tzinfo=UTC
    )


def test_an_instant_that_is_the_next_day_in_london_is_the_same_day_in_new_york() -> (
    None
):
    set_local_timezone("America/New_York")

    # 2026-07-20 23:30 UTC is 00:30 BST on the 21st but 19:30 EDT on the 20th.
    assert to_local_date(datetime(2026, 7, 20, 23, 30, tzinfo=UTC)) == date(2026, 7, 20)


def test_with_new_york_configured_the_us_spring_forward_date_has_forty_six_slots() -> (
    None
):
    set_local_timezone("America/New_York")

    assert expected_half_hour_count(date(2026, 3, 8)) == 46


def test_with_new_york_configured_the_us_fall_back_date_has_fifty_slots() -> None:
    set_local_timezone("America/New_York")

    assert expected_half_hour_count(date(2026, 11, 1)) == 50


def test_with_new_york_configured_an_ordinary_day_has_forty_eight_slots() -> None:
    set_local_timezone("America/New_York")

    assert expected_half_hour_count(date(2026, 7, 20)) == 48


def test_an_unknown_timezone_is_rejected_naming_it_and_leaves_the_zone_unchanged() -> (
    None
):
    set_local_timezone("America/New_York")

    with pytest.raises(ValueError, match="Not/AZone"):
        set_local_timezone("Not/AZone")

    assert get_local_timezone() == ZoneInfo("America/New_York")


def test_the_default_zone_is_europe_london() -> None:
    assert DEFAULT_TIMEZONE == "Europe/London"
    assert get_local_timezone() == ZoneInfo(DEFAULT_TIMEZONE)


def test_an_ordinary_day_has_twenty_four_hour_end_stamps_from_01_to_next_midnight() -> (
    None
):
    stamps = hour_end_stamps(date(2026, 1, 6))

    assert len(stamps) == 24
    assert stamps[0] == datetime(2026, 1, 6, 1, tzinfo=UTC)
    assert stamps[-1] == datetime(2026, 1, 7, 0, tzinfo=UTC)


def test_the_uk_spring_forward_day_has_twenty_three_hour_end_stamps() -> None:
    assert len(hour_end_stamps(date(2026, 3, 29))) == 23


def test_the_uk_fall_back_day_has_twenty_five_hour_end_stamps() -> None:
    assert len(hour_end_stamps(date(2026, 10, 25))) == 25


def test_with_new_york_configured_the_us_clock_change_days_have_23_and_25_stamps() -> (
    None
):
    set_local_timezone("America/New_York")

    assert len(hour_end_stamps(date(2026, 3, 8))) == 23
    assert len(hour_end_stamps(date(2026, 11, 1))) == 25

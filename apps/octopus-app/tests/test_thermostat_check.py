from datetime import UTC, date, datetime, timedelta

from octopus_app.data.thermostat_check import (
    HeatingReading,
    ModelLabel,
    ThermostatCheck,
    classify_days,
    label_model,
    thermostat_threshold,
)

POLL = timedelta(minutes=2)
DAY = date(2026, 1, 15)  # winter: the London day starts at 00:00 UTC


def _readings(
    day: date, *, count: int = 720, on_from: int = 0, on_count: int = 0, boost: bool
) -> list[HeatingReading]:
    """`count` two-minute readings from midnight of `day`; the `on_count`
    readings from index `on_from` are demanding heat."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return [
        HeatingReading(
            polled_at=start + i * POLL,
            state="ON" if on_from <= i < on_from + on_count else "OFF",
            boost_active=boost and on_from <= i < on_from + on_count,
        )
        for i in range(count)
    ]


def test_boost_only_demand_does_not_make_a_day_a_heating_day() -> None:
    readings = _readings(DAY, on_count=60, boost=True)

    assert classify_days(readings) == {DAY: False}


def test_no_readings_classify_no_days() -> None:
    assert classify_days([]) == {}


def test_a_day_with_too_few_readings_is_not_classified() -> None:
    readings = _readings(DAY, count=100, on_count=60, boost=False)

    assert classify_days(readings) == {}


def test_648_readings_classify_a_day() -> None:
    readings = _readings(DAY, count=648, on_count=60, boost=False)

    assert classify_days(readings) == {DAY: True}


def test_647_readings_do_not_classify_a_day() -> None:
    readings = _readings(DAY, count=647, on_count=60, boost=False)

    assert classify_days(readings) == {}


def test_exactly_fifteen_demand_readings_make_a_heating_day() -> None:
    readings = _readings(DAY, on_count=15, boost=False)

    assert classify_days(readings) == {DAY: True}


def test_fourteen_demand_readings_do_not_make_a_heating_day() -> None:
    readings = _readings(DAY, on_count=14, boost=False)

    assert classify_days(readings) == {DAY: False}


def test_readings_over_two_local_days_classify_per_day() -> None:
    next_day = DAY + timedelta(days=1)
    readings = _readings(DAY, on_count=60, boost=False) + _readings(
        next_day, on_count=0, boost=False
    )

    assert classify_days(readings) == {DAY: True, next_day: False}


def test_summer_demand_just_after_local_midnight_belongs_to_that_local_day() -> None:
    # London is UTC+1 in summer: 23:00 UTC on 14 July is 00:00 on 15 July. The
    # only demand is in that first local hour, which is still 14 July in UTC.
    start = datetime(2026, 7, 14, 23, 0, tzinfo=UTC)
    readings = [
        HeatingReading(
            polled_at=start + i * POLL,
            state="ON" if i < 30 else "OFF",
            boost_active=False,
        )
        for i in range(720)
    ]

    assert classify_days(readings) == {date(2026, 7, 15): True}


def _day(n: int) -> date:
    return DAY + timedelta(days=n)


def test_the_threshold_lies_between_separated_heating_and_non_heating_days() -> None:
    flags = {_day(0): True, _day(1): True, _day(2): False, _day(3): False}
    effective = {_day(0): 4.0, _day(1): 8.0, _day(2): 14.0, _day(3): 18.0}

    assert thermostat_threshold(flags, effective) == ThermostatCheck(
        threshold=11.0, heating_days=2, non_heating_days=2
    )


def test_a_day_with_a_non_finite_temperature_is_skipped() -> None:
    flags = {_day(0): True, _day(1): False, _day(2): False}
    effective = {_day(0): 4.0, _day(1): 14.0, _day(2): float("nan")}

    assert thermostat_threshold(flags, effective) == ThermostatCheck(
        threshold=9.0, heating_days=1, non_heating_days=1
    )


def test_there_is_no_threshold_without_both_kinds_of_day() -> None:
    flags = {_day(0): True, _day(1): True}
    effective = {_day(0): 4.0, _day(1): 8.0}

    assert thermostat_threshold(flags, effective) is None


def test_there_is_no_threshold_when_every_day_has_the_same_temperature() -> None:
    flags = {_day(0): True, _day(1): False}
    effective = {_day(0): 10.0, _day(1): 10.0}

    assert thermostat_threshold(flags, effective) is None


def test_only_days_with_both_a_classification_and_an_effective_temperature_count() -> (
    None
):
    flags = {_day(0): True, _day(1): False, _day(2): False}
    effective = {_day(0): 4.0, _day(1): 14.0, _day(3): 30.0}

    assert thermostat_threshold(flags, effective) == ThermostatCheck(
        threshold=9.0, heating_days=1, non_heating_days=1
    )


def test_two_equally_good_cuts_resolve_to_the_colder_one() -> None:
    # Cuts 4 and 16 each misclassify one day, cut 9 two. The midpoint of 4..16
    # (10) would misclassify two days, so the result is a real best cut: 4.
    flags = {_day(0): True, _day(1): True, _day(2): False, _day(3): False}
    effective = {_day(0): 2.0, _day(1): 12.0, _day(2): 6.0, _day(3): 20.0}

    check = thermostat_threshold(flags, effective)

    assert check is not None
    assert check.threshold == 4.0


def test_among_several_equally_good_cuts_the_one_nearest_their_middle_wins() -> None:
    # Cuts 4, 12 and 20 each misclassify two days (8 and 16 three): 12 is the
    # best cut nearest the middle of 4..20.
    heating = {2.0, 10.0, 18.0}
    effective = dict(enumerate([2.0, 6.0, 10.0, 14.0, 18.0, 22.0]))
    flags = {i: t in heating for i, t in effective.items()}

    check = thermostat_threshold(
        {_day(i): flag for i, flag in flags.items()},
        {_day(i): t for i, t in effective.items()},
    )

    assert check is not None
    assert check.threshold == 12.0


def test_overlapping_days_take_the_cut_with_the_fewest_misclassified_days() -> None:
    # Cut 14.5 misclassifies only the non-heating day at 10; every other cut
    # misclassifies at least two days.
    flags = {
        _day(0): True,
        _day(1): True,
        _day(2): True,
        _day(3): True,
        _day(4): False,
        _day(5): False,
        _day(6): False,
    }
    effective = {
        _day(0): 4.0,
        _day(1): 8.0,
        _day(2): 12.0,
        _day(3): 13.0,
        _day(4): 10.0,
        _day(5): 16.0,
        _day(6): 20.0,
    }

    check = thermostat_threshold(flags, effective)

    assert check is not None
    assert check.threshold == 14.5


def _check(
    threshold: float = 12.0, heating_days: int = 15, non_heating_days: int = 15
) -> ThermostatCheck:
    return ThermostatCheck(threshold, heating_days, non_heating_days)


def test_agreement_within_the_tolerance_with_enough_days_is_confirmed() -> None:
    assert label_model(12.0, _check(13.5)) == ModelLabel.CONFIRMED
    assert label_model(12.0, _check(10.5)) == ModelLabel.CONFIRMED


def test_a_gap_of_exactly_the_tolerance_is_confirmed_despite_float_rounding() -> None:
    # 16.1 - 14.6 computes as 1.5000000000000018 in floating point.
    assert label_model(14.6, _check(16.1)) == ModelLabel.CONFIRMED


def test_disagreement_beyond_the_tolerance_is_estimated() -> None:
    assert label_model(12.0, _check(13.6)) == ModelLabel.ESTIMATED
    assert label_model(12.0, _check(10.4)) == ModelLabel.ESTIMATED


def test_fifteen_heating_and_non_heating_days_each_are_enough_evidence() -> None:
    assert label_model(12.0, _check(12.0, 15, 15)) == ModelLabel.CONFIRMED


def test_fourteen_heating_days_are_not_enough_evidence() -> None:
    assert label_model(12.0, _check(12.0, 14, 15)) == ModelLabel.ESTIMATED


def test_fourteen_non_heating_days_are_not_enough_evidence() -> None:
    assert label_model(12.0, _check(12.0, 15, 14)) == ModelLabel.ESTIMATED


def test_without_a_thermostat_check_the_model_is_estimated() -> None:
    assert label_model(12.0, None) == ModelLabel.ESTIMATED


def test_without_a_gas_threshold_the_model_is_estimated() -> None:
    assert label_model(None, _check()) == ModelLabel.ESTIMATED


def test_with_little_heating_history_the_model_is_estimated() -> None:
    flags = {_day(0): True, _day(1): False}
    effective = {_day(0): 4.0, _day(1): 18.0}

    assert label_model(11.0, thermostat_threshold(flags, effective)) == (
        ModelLabel.ESTIMATED
    )

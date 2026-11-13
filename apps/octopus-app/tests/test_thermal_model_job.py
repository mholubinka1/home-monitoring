"""The Thermal Model job: five numbers learned from the thermostat, the heating
hours and the hourly weather, stored one row per fit (ADR-0035).

Histories are generated from known model numbers, so a test can ask the job to
recover them. London is on GMT throughout, so local time is UTC."""

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest

from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import (
    MariaDBClient,
    heating_status_table,
    weather_observation_table,
)
from octopus_app.data.thermal_job import ThermalModelJob

NOW = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
ONE_HOUR = timedelta(hours=1)
LOCATION = "51.50,-0.10"


@dataclass(frozen=True)
class Numbers:
    """The five numbers of a thermal model."""

    loss_per_degree: float = 0.02
    heating_gain: float = 1.3
    carried_gain: float = 0.3
    sun_gain: float = 0.4
    constant_gain: float = 0.08


@dataclass(frozen=True)
class Hour:
    """One generated hour: the room at its start, the outdoor temperature at its
    start, the mean radiation over it (W/m2) and the minutes of heating in it."""

    start: datetime
    room: float
    outdoor: float
    radiation: float
    on_minutes: int


def _outdoor(hour: datetime) -> float:
    daily = 5 * math.sin(2 * math.pi * (hour.hour - 9) / 24)
    weekly = 2 * math.sin(2 * math.pi * hour.toordinal() / 7)
    return 7 + daily + weekly


def _radiation(hour: datetime) -> float:
    if not 8 <= hour.hour < 16:
        return 0.0
    brightness = 0.5 + (hour.toordinal() % 5) / 8
    return 250 * brightness * math.sin(math.pi * (hour.hour - 8) / 8)


def _on_minutes(hour: datetime) -> int:
    day = hour.toordinal()
    schedule = {6: 40, 7: 10}
    if day % 2 == 0:
        schedule[18] = 30
    if day % 3 == 0:
        schedule[21] = 20
    return schedule.get(hour.hour, 0)


def _rarely_on_minutes(hour: datetime) -> int:
    return 30 if hour.hour == 6 and hour.toordinal() % 3 == 0 else 0


def _always_on_minutes(_: datetime) -> int:
    return 60


def _constant_outdoor(_: datetime) -> float:
    return 10.0


def _no_radiation(_: datetime) -> float:
    return 0.0


@dataclass(frozen=True)
class Conditions:
    """What the house and the weather did, hour by hour."""

    on_minutes: Callable[[datetime], int] = _on_minutes
    outdoor: Callable[[datetime], float] = _outdoor
    radiation: Callable[[datetime], float] = _radiation


STEADY = Conditions(
    on_minutes=_always_on_minutes, outdoor=_constant_outdoor, radiation=_no_radiation
)
RARELY_HEATED = Conditions(on_minutes=_rarely_on_minutes)


@dataclass(frozen=True)
class Shift:
    """From `at` on, the house follows different numbers."""

    at: datetime
    numbers: Numbers


def _history(
    days: int,
    numbers: Numbers,
    *,
    end: datetime = NOW,
    conditions: Conditions = Conditions(),  # noqa: B008
    shift: Shift | None = None,
) -> list[Hour]:
    """Every hour of the `days` before the hour of `end`, plus that hour itself,
    with a room that follows `numbers` exactly."""
    last = end.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    start = last - timedelta(days=days)
    hours: list[Hour] = []
    room = 17.0
    previous_on = 0
    moment = start
    while moment <= last:
        hour = Hour(
            start=moment,
            room=room,
            outdoor=conditions.outdoor(moment),
            radiation=conditions.radiation(moment),
            on_minutes=conditions.on_minutes(moment),
        )
        hours.append(hour)
        active = shift.numbers if shift and moment >= shift.at else numbers
        room += (
            active.loss_per_degree * (hour.outdoor - hour.room)
            + active.heating_gain * hour.on_minutes / 60
            + active.carried_gain * previous_on / 60
            + active.sun_gain * hour.radiation / 1000
            + active.constant_gain
        )
        previous_on = hour.on_minutes
        moment += ONE_HOUR
    return hours


@dataclass(frozen=True)
class Gaps:
    """Hour starts whose reading, heating hour or weather is left out of the seed."""

    reading: frozenset[datetime] = frozenset()
    heating_hour: frozenset[datetime] = frozenset()
    weather: frozenset[datetime] = frozenset()


def _seed(
    client: MariaDBClient,
    hours: list[Hour],
    gaps: Gaps = Gaps(),  # noqa: B008
) -> None:
    """Stores what hive-app and the heating-hour job would have: a thermostat
    reading a minute into each hour, a heating hour for each completed hour, and
    the hourly weather (the radiation stamped at the end of its hour)."""
    polls = [
        {
            "polled_at": hour.start + timedelta(minutes=1),
            "current_temp": hour.room,
            "target_temp": 7.0,
            "mode": "SCHEDULE",
            "state": "OFF",
            "boost_active": False,
        }
        for hour in hours
        if hour.start not in gaps.reading
    ]
    radiation_by_stamp = {hour.start + ONE_HOUR: hour.radiation for hour in hours}
    weather = [
        {
            "source": "open-meteo",
            "location": LOCATION,
            "observed_at": hour.start,
            "temp": hour.outdoor,
            "shortwave_radiation": radiation_by_stamp.get(hour.start),
        }
        for hour in hours
        if hour.start not in gaps.weather
    ]
    with client.session_write_scope() as session:
        session.execute(heating_status_table.insert(), polls)
        if weather:
            session.execute(weather_observation_table.insert(), weather)
        session.add_all(
            model.heating_hour(
                hour_start=hour.start,
                poll_count=30,
                on_minutes=hour.on_minutes,
                source="estimated",
                computed_at=hour.start,
            )
            for hour in hours[:-1]
            if hour.start not in gaps.heating_hour
        )


def _thermal_models(client: MariaDBClient) -> list[model.thermal_model]:
    with client.session_read_scope() as session:
        return session.query(model.thermal_model).order_by(model.thermal_model.id).all()


def test_the_model_recovers_the_numbers_the_history_was_generated_from(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 30 days of history that follows known numbers exactly
    numbers = Numbers()
    _seed(mariadb_client, _history(30, numbers))

    # When the job runs
    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then one model is stored with those numbers
    [stored] = _thermal_models(mariadb_client)
    assert stored.loss_per_degree == pytest.approx(numbers.loss_per_degree, abs=1e-6)
    assert stored.heating_gain == pytest.approx(numbers.heating_gain, abs=1e-6)
    assert stored.carried_gain == pytest.approx(numbers.carried_gain, abs=1e-6)
    assert stored.sun_gain == pytest.approx(numbers.sun_gain, abs=1e-6)
    assert stored.constant_gain == pytest.approx(numbers.constant_gain, abs=1e-6)
    assert stored.usable
    assert stored.unusable_reason is None


def test_hours_with_incomplete_readings_are_left_out_and_the_numbers_still_recovered(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the same history with an hour missing its early reading, another
    # missing its heating hour and another missing its weather
    numbers = Numbers()
    hours = _history(10, numbers)
    gaps = Gaps(
        reading=frozenset({hours[48].start, hours[100].start}),
        heating_hour=frozenset({hours[60].start}),
        weather=frozenset({hours[120].start}),
    )
    _seed(mariadb_client, hours, gaps)

    # When the job runs
    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then the hours are left out and the numbers are still recovered
    [stored] = _thermal_models(mariadb_client)
    # 239 steps in all; each gap takes out the two steps that touch its hour
    assert stored.steps_used == 239 - 2 * 4
    assert stored.loss_per_degree == pytest.approx(numbers.loss_per_degree, abs=1e-6)
    assert stored.heating_gain == pytest.approx(numbers.heating_gain, abs=1e-6)
    assert stored.carried_gain == pytest.approx(numbers.carried_gain, abs=1e-6)
    assert stored.sun_gain == pytest.approx(numbers.sun_gain, abs=1e-6)
    assert stored.constant_gain == pytest.approx(numbers.constant_gain, abs=1e-6)


def test_under_a_week_of_steps_is_unusable_for_too_few_steps(
    mariadb_client: MariaDBClient,
) -> None:
    _seed(mariadb_client, _history(5, Numbers()))

    ThermalModelJob(mariadb_client).refresh(NOW)

    [stored] = _thermal_models(mariadb_client)
    assert not stored.usable
    assert stored.unusable_reason == "too_few_steps"


def test_a_history_with_almost_no_heating_is_unusable_for_too_little_heating(
    mariadb_client: MariaDBClient,
) -> None:
    _seed(mariadb_client, _history(10, Numbers(), conditions=RARELY_HEATED))

    ThermalModelJob(mariadb_client).refresh(NOW)

    [stored] = _thermal_models(mariadb_client)
    assert not stored.usable
    assert stored.unusable_reason == "too_little_heating"


def test_a_history_whose_inputs_do_not_vary_is_unusable_for_no_fit(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a room that never changes, steady heating and steady weather
    steady = Numbers(
        loss_per_degree=0.0,
        heating_gain=1.0,
        carried_gain=0.0,
        sun_gain=0.0,
        constant_gain=-1.0,
    )
    _seed(mariadb_client, _history(10, steady, conditions=STEADY))

    ThermalModelJob(mariadb_client).refresh(NOW)

    [stored] = _thermal_models(mariadb_client)
    assert not stored.usable
    assert stored.unusable_reason == "no_fit"


def test_a_history_in_which_heating_cools_the_room_is_unusable_as_implausible(
    mariadb_client: MariaDBClient,
) -> None:
    _seed(mariadb_client, _history(10, Numbers(heating_gain=-0.5)))

    ThermalModelJob(mariadb_client).refresh(NOW)

    [stored] = _thermal_models(mariadb_client)
    assert not stored.usable
    assert stored.unusable_reason == "implausible"


def test_a_fit_is_written_once_a_day_and_again_on_the_next_day(
    mariadb_client: MariaDBClient,
) -> None:
    _seed(mariadb_client, _history(10, Numbers()))
    job = ThermalModelJob(mariadb_client)
    job.refresh(NOW)

    job.refresh(NOW + ONE_HOUR)
    assert len(_thermal_models(mariadb_client)) == 1

    job.refresh(NOW + timedelta(days=1))
    assert len(_thermal_models(mariadb_client)) == 2


def test_nothing_is_written_without_thermostat_readings(
    mariadb_client: MariaDBClient,
) -> None:
    ThermalModelJob(mariadb_client).refresh(NOW)

    assert not _thermal_models(mariadb_client)


def test_nothing_is_written_or_raised_when_hive_apps_tables_are_missing(
    mariadb_client_without_weather_tables: MariaDBClient,
) -> None:
    client = mariadb_client_without_weather_tables

    ThermalModelJob(client).refresh(NOW)

    assert not _thermal_models(client)


def test_nothing_is_written_while_there_is_no_hourly_weather_at_all(
    mariadb_client: MariaDBClient,
) -> None:
    # Given thermostat readings and heating hours, but no weather stored yet
    hours = _history(30, Numbers())
    every_hour = frozenset(hour.start for hour in hours)
    _seed(mariadb_client, hours, Gaps(weather=every_hour))

    ThermalModelJob(mariadb_client).refresh(NOW)

    assert not _thermal_models(mariadb_client)


def test_a_history_that_follows_the_model_backtests_with_almost_no_error(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 30 days of history that follows known numbers exactly
    _seed(mariadb_client, _history(30, Numbers()))

    # When the job runs
    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then each of the 14 days before today was replayed, and the replays agree
    # with what the thermostat recorded
    [stored] = _thermal_models(mariadb_client)
    assert stored.backtest_days == 14
    assert stored.backtest_mean_error == pytest.approx(0.0, abs=1e-6)
    assert stored.backtest_worst_error == pytest.approx(0.0, abs=1e-6)
    assert stored.usable


def test_a_backtest_day_with_a_gap_in_its_hours_is_skipped_and_the_count_says_so(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 30 days of history in which one hour of a backtest day has no reading
    hours = _history(30, Numbers())
    missing = datetime(2026, 11, 10, 12)
    _seed(mariadb_client, hours, Gaps(reading=frozenset({missing})))

    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then that day is skipped and the other 13 are replayed
    [stored] = _thermal_models(mariadb_client)
    assert stored.backtest_days == 13
    assert stored.usable


def test_fewer_than_five_backtestable_days_is_unusable_for_too_few_backtest_days(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 10 days of history: only the last few backtest days have enough steps
    # before them for an earlier model to learn from
    _seed(mariadb_client, _history(10, Numbers()))

    ThermalModelJob(mariadb_client).refresh(NOW)

    [stored] = _thermal_models(mariadb_client)
    assert stored.backtest_days is not None
    assert 0 < stored.backtest_days < 5
    assert not stored.usable
    assert stored.unusable_reason == "too_few_backtest_days"


def test_exactly_five_backtestable_days_is_enough(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 13 days of history: a week of steps exists before each of the last
    # five backtest days, and before none of the earlier ones
    _seed(mariadb_client, _history(13, Numbers()))

    ThermalModelJob(mariadb_client).refresh(NOW)

    [stored] = _thermal_models(mariadb_client)
    assert stored.backtest_days == 5
    assert stored.usable


def test_a_backtest_across_a_clock_change_replays_the_25_hour_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 40 days of history ending a week after the clocks go back (25 October
    # 2026), so the 14 backtest days include the 25-hour day and days on BST
    a_week_after_the_change = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
    _seed(mariadb_client, _history(40, Numbers(), end=a_week_after_the_change))

    ThermalModelJob(mariadb_client).refresh(a_week_after_the_change)

    # Then every one of the 14 days is replayed, with almost no error
    [stored] = _thermal_models(mariadb_client)
    assert stored.backtest_days == 14
    assert stored.backtest_mean_error == pytest.approx(0.0, abs=1e-6)


def test_a_last_fortnight_that_behaves_differently_is_unusable_for_a_high_backtest_error(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 60 days that follow one set of numbers, then a fortnight in which the
    # room loses heat much faster
    leaky = Shift(at=datetime(2026, 11, 6), numbers=Numbers(loss_per_degree=0.2))
    _seed(mariadb_client, _history(74, Numbers(), shift=leaky))

    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then the earlier models mispredict the fortnight
    [stored] = _thermal_models(mariadb_client)
    assert stored.backtest_mean_error is not None
    assert stored.backtest_mean_error > 0.5
    assert not stored.usable
    assert stored.unusable_reason == "backtest_error_too_high"


def test_a_backtest_day_is_replayed_by_a_model_that_has_not_seen_it(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a history that follows the numbers exactly except that the thermostat
    # read 2 C warm all through the newest backtest day after its first hour
    hours = _history(30, Numbers())
    newest_day = datetime(2026, 11, 19)
    warm = [
        (
            replace(hour, room=hour.room + 2.0)
            if newest_day < hour.start < newest_day + timedelta(hours=24)
            else hour
        )
        for hour in hours
    ]
    _seed(mariadb_client, warm)

    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then every day is still replayed, and only that day's hours are in error:
    # the model that replayed it was fitted before the day, so it is still exact
    [stored] = _thermal_models(mariadb_client)
    assert stored.backtest_days == 14
    assert stored.backtest_worst_error == pytest.approx(2.0, abs=1e-6)
    assert stored.backtest_mean_error == pytest.approx(23 * 2.0 / (14 * 24), abs=1e-6)

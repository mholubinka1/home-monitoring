"""The Thermal Model job: five numbers learned from the thermostat polls and the
hourly weather on 10-minute steps, stored one row per fit (ADR-0035, ADR-0036).

Histories are generated from known model numbers, so a test can ask the job to
recover them. London is on GMT throughout, so local time is UTC."""

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

import pytest

from octopus_app.data.model import ThermalModelRecord
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import (
    MariaDBClient,
    heating_status_table,
    weather_observation_table,
)
from octopus_app.data.thermal_job import ThermalModelJob

NOW = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
ONE_HOUR = timedelta(hours=1)
STEP = timedelta(minutes=10)
POLL = timedelta(minutes=2)
STEPS_A_DAY = 144
# The heating of a step is read from the polls up to 40 minutes before it, and the
# step before that, so the steps of a history's first 40 minutes are incomplete.
STEPS_LOST_AT_START = 5
LOCATION = "51.50,-0.10"


@dataclass(frozen=True)
class Numbers:
    """The five numbers of a thermal model, per hour."""

    loss_per_degree: float = 0.02
    heating_gain: float = 1.3
    carried_gain: float = 0.3
    sun_gain: float = 0.4
    constant_gain: float = 0.08


@dataclass(frozen=True)
class GeneratedStep:
    """One generated 10-minute step: the room and the outdoor temperature at its
    start, the mean radiation (W/m2) of its hour and the polls with the heating on,
    out of its five."""

    start: datetime
    room: float
    outdoor: float
    radiation: float
    on_polls: int


def _outdoor(hour: datetime) -> float:
    daily = 5 * math.sin(2 * math.pi * (hour.hour - 9) / 24)
    weekly = 2 * math.sin(2 * math.pi * hour.toordinal() / 7)
    return 7 + daily + weekly


def _radiation(hour: datetime) -> float:
    if not 8 <= hour.hour < 16:
        return 0.0
    brightness = 0.5 + (hour.toordinal() % 5) / 8
    return 250 * brightness * math.sin(math.pi * (hour.hour - 8) / 8)


def _on_polls(mark: datetime) -> int:
    """Between zero and five polls on in the step, on some mornings and evenings."""
    day = mark.toordinal()
    slot = mark.hour * 6 + mark.minute // 10
    if 6 * 6 <= slot < 7 * 6:  # 06:00 to 07:00: the heating is on, a little less
        return 5 if slot % 6 < 4 else 3
    if 18 * 6 <= slot < 19 * 6 and day % 2 == 0:
        return 5 if slot % 6 < 3 else 2
    if 21 * 6 <= slot < 21 * 6 + 3 and day % 3 == 0:
        return 4
    return 0


def _rarely_on_polls(mark: datetime) -> int:
    in_the_morning = mark.hour == 6 and mark.minute < 30
    return 5 if in_the_morning and mark.toordinal() % 3 == 0 else 0


def _always_on_polls(_: datetime) -> int:
    return 5


def _constant_outdoor(_: datetime) -> float:
    return 10.0


def _no_radiation(_: datetime) -> float:
    return 0.0


@dataclass(frozen=True)
class Conditions:
    """What the house and the weather did."""

    on_polls: Callable[[datetime], int] = _on_polls
    outdoor: Callable[[datetime], float] = _outdoor
    radiation: Callable[[datetime], float] = _radiation


STEADY = Conditions(
    on_polls=_always_on_polls, outdoor=_constant_outdoor, radiation=_no_radiation
)
RARELY_HEATED = Conditions(on_polls=_rarely_on_polls)


@dataclass(frozen=True)
class Shift:
    """From `at` on, the house follows different numbers."""

    at: datetime
    numbers: Numbers


def _outdoor_at(conditions: Conditions, mark: datetime) -> float:
    """The outdoor temperature at a mark: a straight line between the hourly stamps."""
    hour = mark.replace(minute=0)
    fraction = (mark - hour) / ONE_HOUR
    return conditions.outdoor(hour) + fraction * (
        conditions.outdoor(hour + ONE_HOUR) - conditions.outdoor(hour)
    )


def _poll_is_on(conditions: Conditions, at: datetime) -> bool:
    """Whether the thermostat polled at that time (a 2-minute mark) calls for heat:
    the first polls of each step."""
    mark = at.replace(minute=at.minute // 10 * 10)
    return (at - mark) // POLL < conditions.on_polls(mark)


def _heating_minutes(conditions: Conditions, begin: datetime, delay: int) -> int:
    """The minutes of heating that reach the room in the 10 minutes from `begin`:
    those the thermostat asked for `delay` minutes before."""
    first = begin - timedelta(minutes=delay)
    return 2 * sum(_poll_is_on(conditions, first + POLL * poll) for poll in range(5))


def _history(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    days: int,
    numbers: Numbers,
    *,
    end: datetime = NOW,
    conditions: Conditions = Conditions(),
    shift: Shift | None = None,
    delay_minutes: int = 0,
) -> list[GeneratedStep]:
    """Every 10-minute step of the `days` before the hour of `end`, plus the step
    that starts at that hour, with a room that follows `numbers` exactly, the
    heating reaching it `delay_minutes` after the thermostat asked for it."""
    last = end.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    steps: list[GeneratedStep] = []
    room = 17.0
    mark = last - timedelta(days=days)
    while mark <= last:
        step = GeneratedStep(
            start=mark,
            room=room,
            outdoor=_outdoor_at(conditions, mark),
            radiation=conditions.radiation(mark.replace(minute=0)),
            on_polls=conditions.on_polls(mark),
        )
        steps.append(step)
        active = shift.numbers if shift and mark >= shift.at else numbers
        room += (
            active.loss_per_degree * (step.outdoor - step.room)
            + active.sun_gain * step.radiation / 1000
            + active.constant_gain
        ) / 6 + (
            active.heating_gain * _heating_minutes(conditions, mark, delay_minutes)
            + active.carried_gain
            * _heating_minutes(conditions, mark - STEP, delay_minutes)
        ) / 60
        mark += STEP
    return steps


@dataclass(frozen=True)
class Gaps:
    """Marks (step starts) and hours left out of the seed."""

    room_temperature: frozenset[datetime] = frozenset()  # first poll has no temp
    thin_polls: frozenset[datetime] = frozenset()  # only three polls in the step
    weather: frozenset[datetime] = frozenset()  # hours with no weather stamp


def _poll_rows(steps: list[GeneratedStep], gaps: Gaps) -> list[dict[str, object]]:
    rows = []
    for index, step in enumerate(steps):
        following = steps[index + 1].room if index + 1 < len(steps) else step.room
        for poll in range(5):
            if step.start in gaps.thin_polls and poll in (3, 4):
                continue
            is_on = poll < step.on_polls
            temp: float | None = step.room + (following - step.room) * poll / 5
            if poll == 0:
                temp = step.room
            if step.start in gaps.room_temperature and poll == 0:
                temp = None
            rows.append(
                {
                    "polled_at": step.start + POLL * poll,
                    "current_temp": temp,
                    "target_temp": 21.0 if is_on else 7.0,
                    "mode": "SCHEDULE",
                    "state": "ON" if is_on else "OFF",
                    "boost_active": False,
                }
            )
    return rows


def _seed(
    client: MariaDBClient,
    steps: list[GeneratedStep],
    gaps: Gaps = Gaps(),
) -> None:
    """Stores what hive-app would have: a poll every 2 minutes, and the hourly
    weather (the radiation stamped at the end of its hour)."""
    radiation_by_stamp = {
        step.start.replace(minute=0) + ONE_HOUR: step.radiation for step in steps
    }
    stamps = sorted({step.start.replace(minute=0) for step in steps})
    weather = [
        {
            "source": "open-meteo",
            "location": LOCATION,
            "observed_at": stamp,
            "temp": _outdoor_for(stamp, steps),
            "shortwave_radiation": radiation_by_stamp.get(stamp),
        }
        for stamp in stamps
        if stamp not in gaps.weather
    ]
    with client.session_write_scope() as session:
        session.execute(heating_status_table.insert(), _poll_rows(steps, gaps))
        if weather:
            session.execute(weather_observation_table.insert(), weather)


def _outdoor_for(hour: datetime, steps: list[GeneratedStep]) -> float:
    """The outdoor temperature at an hour stamp, as the generator had it."""
    return next(step.outdoor for step in steps if step.start == hour)


def _thermal_models(client: MariaDBClient) -> list[model.thermal_model]:
    with client.session_read_scope() as session:
        return session.query(model.thermal_model).order_by(model.thermal_model.id).all()


def _assert_numbers_recovered(stored: model.thermal_model, numbers: Numbers) -> None:
    assert stored.loss_per_degree == pytest.approx(numbers.loss_per_degree, abs=1e-6)
    assert stored.heating_gain == pytest.approx(numbers.heating_gain, abs=1e-6)
    assert stored.carried_gain == pytest.approx(numbers.carried_gain, abs=1e-6)
    assert stored.sun_gain == pytest.approx(numbers.sun_gain, abs=1e-6)
    assert stored.constant_gain == pytest.approx(numbers.constant_gain, abs=1e-6)


def test_the_model_recovers_the_per_hour_numbers_the_history_was_generated_from(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 30 days of polls that follow known per-hour numbers exactly
    numbers = Numbers()
    _seed(mariadb_client, _history(30, numbers))

    # When the job runs
    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then one model is stored with those numbers, per hour, fitted on 10-minute
    # steps; the first steps have no polls before them to say what the heating was
    [stored] = _thermal_models(mariadb_client)
    _assert_numbers_recovered(stored, numbers)
    assert stored.step_minutes == 10
    assert stored.steps_used == 30 * STEPS_A_DAY - STEPS_LOST_AT_START
    assert stored.usable
    assert stored.unusable_reason is None


def test_steps_with_incomplete_readings_are_left_out_and_the_numbers_still_recovered(
    mariadb_client: MariaDBClient,
) -> None:
    # Given the same history with a step whose first poll has no temperature, a step
    # with only three of its five polls, and an hour with no weather
    numbers = Numbers()
    steps = _history(10, numbers)
    no_temperature = steps[480].start
    thin = steps[900].start
    no_weather = steps[1200].start.replace(minute=0)
    _seed(
        mariadb_client,
        steps,
        Gaps(
            room_temperature=frozenset({no_temperature}),
            thin_polls=frozenset({thin}),
            weather=frozenset({no_weather}),
        ),
    )

    # When the job runs
    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then each is left out with its neighbours that share the missing reading, the
    # span before it or the stamp, and the numbers are still recovered:
    # 1,440 steps, less those of the first 40 minutes, and
    # two for the temperature (that step and the one before it, which ends there),
    # six for the thin polls (that step and the five after it, each of which reads
    # them as its heating or the heating before it at one of the searched delays),
    # and twelve for the weather stamp (the hour before and the hour)
    [stored] = _thermal_models(mariadb_client)
    assert stored.steps_used == 10 * STEPS_A_DAY - STEPS_LOST_AT_START - 2 - 6 - 12
    _assert_numbers_recovered(stored, numbers)


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
    # Given 10 days in which the heating comes on for 10 minutes on only 3 mornings
    _seed(mariadb_client, _history(10, Numbers(), conditions=RARELY_HEATED))

    ThermalModelJob(mariadb_client).refresh(NOW)

    [stored] = _thermal_models(mariadb_client)
    assert not stored.usable
    assert stored.unusable_reason == "too_little_heating"


@pytest.mark.parametrize(
    ("polls_on", "heating_steps"),
    [(2, 60), (1, 0)],
    ids=["four minutes is a heating step", "two minutes is not"],
)
def test_a_step_is_a_heating_step_from_four_minutes_of_heating(
    mariadb_client: MariaDBClient, polls_on: int, heating_steps: int
) -> None:
    # Given `polls_on` polls on at midday every day, and no other heating
    def midday(mark: datetime) -> int:
        return polls_on if mark.hour == 12 and mark.minute == 0 else 0

    _seed(
        mariadb_client, _history(61, Numbers(), conditions=Conditions(on_polls=midday))
    )

    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then the 60 days of the fit hold that many heating steps
    [stored] = _thermal_models(mariadb_client)
    assert stored.heating_steps == heating_steps
    assert (stored.unusable_reason == "too_little_heating") == (heating_steps == 0)


def test_a_history_whose_inputs_do_not_vary_is_unusable_for_no_fit(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a room that never changes, steady heating and steady weather
    steady = Numbers(
        loss_per_degree=0.0,
        heating_gain=6.0,
        carried_gain=0.0,
        sun_gain=0.0,
        constant_gain=-6.0,
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


def test_a_model_fitted_hourly_is_refitted_at_once_even_the_same_day(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a model fitted hourly earlier today, with no step length stored
    _seed(mariadb_client, _history(10, Numbers()))
    mariadb_client.write_thermal_model(
        ThermalModelRecord(
            fitted_at=NOW - timedelta(hours=2),
            window_start=date(2026, 9, 21),
            window_end=date(2026, 11, 19),
            usable=True,
            unusable_reason=None,
            steps_used=1000,
            heating_steps=200,
            loss_per_degree=0.02,
            heating_gain=1.3,
            carried_gain=0.3,
            sun_gain=0.4,
            constant_gain=0.08,
            backtest_days=10,
            backtest_mean_error=0.2,
            backtest_worst_error=0.6,
        )
    )

    # When the job runs
    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then it fits again, now on 10-minute steps, and does not again the next hour
    models = _thermal_models(mariadb_client)
    assert [stored.step_minutes for stored in models] == [None, 10]
    ThermalModelJob(mariadb_client).refresh(NOW + ONE_HOUR)
    assert len(_thermal_models(mariadb_client)) == 2


def test_the_stored_window_is_the_local_days_of_the_first_and_last_fitted_step(
    mariadb_client: MariaDBClient,
) -> None:
    # Given a first run of the day half an hour after local midnight
    just_after_midnight = datetime(2026, 11, 20, 0, 30, tzinfo=UTC)
    _seed(mariadb_client, _history(10, Numbers(), end=just_after_midnight))

    # When the job runs
    ThermalModelJob(mariadb_client).refresh(just_after_midnight)

    # Then the window ends on yesterday, the day of the last fitted step (23:50)
    [stored] = _thermal_models(mariadb_client)
    assert stored.window_start == date(2026, 9, 21)
    assert stored.window_end == date(2026, 11, 19)


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
    # Given thermostat readings, but no weather stored yet
    steps = _history(30, Numbers())
    every_hour = frozenset(step.start.replace(minute=0) for step in steps)
    _seed(mariadb_client, steps, Gaps(weather=every_hour))

    ThermalModelJob(mariadb_client).refresh(NOW)

    assert not _thermal_models(mariadb_client)


def test_a_history_that_follows_the_model_backtests_with_almost_no_error(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 30 days of history that follows known numbers exactly
    _seed(mariadb_client, _history(30, Numbers()))

    # When the job runs
    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then each of the 14 days before today was replayed in 10-minute steps, and the
    # replays agree with what the thermostat recorded
    [stored] = _thermal_models(mariadb_client)
    assert stored.backtest_days == 14
    assert stored.backtest_mean_error == pytest.approx(0.0, abs=1e-6)
    assert stored.backtest_worst_error == pytest.approx(0.0, abs=1e-6)
    assert stored.usable


def test_a_backtest_day_with_a_gap_in_its_steps_is_skipped_and_the_count_says_so(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 30 days of history in which one step of a backtest day has no temperature
    steps = _history(30, Numbers())
    missing = datetime(2026, 11, 10, 12, 0)
    _seed(mariadb_client, steps, Gaps(room_temperature=frozenset({missing})))

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
    # read 2 C warm all through the newest backtest day after its first step
    steps = _history(30, Numbers())
    newest_day = datetime(2026, 11, 19)
    warm = [
        (
            replace(step, room=step.room + 2.0)
            if newest_day < step.start < newest_day + timedelta(hours=24)
            else step
        )
        for step in steps
    ]
    _seed(mariadb_client, warm)

    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then every day is still replayed, and only that day's steps are in error: the
    # model that replayed it was fitted before the day, so it is still exact. The
    # first 143 steps end in a warm reading; the last ends at the next midnight
    [stored] = _thermal_models(mariadb_client)
    assert stored.backtest_days == 14
    assert stored.backtest_worst_error == pytest.approx(2.0, abs=1e-6)
    assert stored.backtest_mean_error == pytest.approx(
        143 * 2.0 / (14 * STEPS_A_DAY), abs=1e-6
    )


def _seed_stored_delay(
    client: MariaDBClient, minutes: int | None, source: str | None
) -> None:
    """An earlier fit, a day before NOW, stored with that delay."""
    client.write_thermal_model(
        ThermalModelRecord(
            fitted_at=NOW - timedelta(days=1),
            window_start=date(2026, 9, 20),
            window_end=date(2026, 11, 18),
            usable=True,
            unusable_reason=None,
            steps_used=1000,
            heating_steps=200,
            loss_per_degree=0.02,
            heating_gain=1.3,
            carried_gain=0.3,
            sun_gain=0.4,
            constant_gain=0.08,
            backtest_days=10,
            backtest_mean_error=0.2,
            backtest_worst_error=0.6,
            step_minutes=10,
            warm_up_delay_minutes=minutes,
            warm_up_delay_source=source,
        )
    )


def test_the_delay_is_learned_from_polls_of_a_house_that_responds_14_minutes_late(
    mariadb_client: MariaDBClient,
) -> None:
    # Given 30 days in which the room responds 14 minutes after the thermostat asks
    # for heat, with the heating coming on every morning (at least 10 Heating Starts)
    numbers = Numbers()
    _seed(mariadb_client, _history(30, numbers, delay_minutes=14))

    # When the job runs
    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then the stored delay is 14, learned, the numbers are the house's, and every
    # backtest day was replayed by a model that learned its own delay
    [stored] = _thermal_models(mariadb_client)
    assert stored.warm_up_delay_minutes == 14
    assert stored.warm_up_delay_source == "learned"
    assert stored.heating_starts >= 10
    _assert_numbers_recovered(stored, numbers)
    assert stored.backtest_days == 14
    assert stored.backtest_mean_error == pytest.approx(0.0, abs=1e-6)
    assert stored.usable


def test_a_house_that_responds_at_once_has_a_learned_delay_of_zero(
    mariadb_client: MariaDBClient,
) -> None:
    _seed(mariadb_client, _history(30, Numbers()))

    ThermalModelJob(mariadb_client).refresh(NOW)

    [stored] = _thermal_models(mariadb_client)
    assert (stored.warm_up_delay_minutes, stored.warm_up_delay_source) == (0, "learned")


FEW_STARTS = (7, 8)  # the mornings with an ordinal divisible by 8 in 60 days


def _few_heating_runs(mark: datetime) -> int:
    """Heating on from 06:00 to 06:50 on one morning in eight: 7 or 8 Heating
    Starts and 35 or 40 heating steps in 60 days."""
    if mark.toordinal() % 8 == 0 and mark.hour == 6 and mark.minute < 50:
        return 5
    return 0


def test_with_too_few_heating_starts_the_newest_stored_delay_carries_on(
    mariadb_client: MariaDBClient,
) -> None:
    # Given an earlier fit that learned 14 minutes, and a history of a house that
    # responds 14 minutes late whose heating came on only on a few mornings
    numbers = Numbers()
    conditions = Conditions(on_polls=_few_heating_runs)
    _seed_stored_delay(mariadb_client, 14, "learned")
    _seed(
        mariadb_client,
        _history(61, numbers, conditions=conditions, delay_minutes=14),
    )

    # When the job runs
    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then the delay is 14 and carried, and the numbers fitted with it are the
    # house's
    stored = _thermal_models(mariadb_client)[-1]
    assert stored.heating_starts in FEW_STARTS
    assert (stored.warm_up_delay_minutes, stored.warm_up_delay_source) == (
        14,
        "carried",
    )
    _assert_numbers_recovered(stored, numbers)


def test_a_carried_delay_is_carried_again_by_the_next_fit(
    mariadb_client: MariaDBClient,
) -> None:
    _seed_stored_delay(mariadb_client, 14, "carried")
    _seed(
        mariadb_client,
        _history(
            61,
            Numbers(),
            conditions=Conditions(on_polls=_few_heating_runs),
            delay_minutes=14,
        ),
    )

    ThermalModelJob(mariadb_client).refresh(NOW)

    stored = _thermal_models(mariadb_client)[-1]
    assert (stored.warm_up_delay_minutes, stored.warm_up_delay_source) == (
        14,
        "carried",
    )


@pytest.mark.parametrize(
    ("earlier_minutes", "earlier_source"),
    [(None, None), (0, "none")],
    ids=["a fit from before the delay existed", "a fit that never had one"],
)
def test_with_too_few_heating_starts_and_no_earlier_delay_the_delay_is_none(
    mariadb_client: MariaDBClient,
    earlier_minutes: int | None,
    earlier_source: str | None,
) -> None:
    # Given a history whose heating came on only a few times, and no earlier fit that
    # learned or carried a delay
    _seed_stored_delay(mariadb_client, earlier_minutes, earlier_source)
    _seed(
        mariadb_client,
        _history(61, Numbers(), conditions=Conditions(on_polls=_few_heating_runs)),
    )

    # When the job runs
    ThermalModelJob(mariadb_client).refresh(NOW)

    # Then the delay is 0 with the source none
    stored = _thermal_models(mariadb_client)[-1]
    assert stored.heating_starts in FEW_STARTS
    assert (stored.warm_up_delay_minutes, stored.warm_up_delay_source) == (0, "none")


def test_heating_that_keeps_coming_on_within_30_minutes_is_one_heating_start(
    mariadb_client: MariaDBClient,
) -> None:
    # Given heating on in two runs 20 minutes apart (06:00 to 06:20 and 06:40 to
    # 07:00) on a morning in eight, which is one Heating Start a morning, 7 or 8 in
    # all (14 or 16 if each run counted, enough to learn a delay)
    def split_morning(mark: datetime) -> int:
        in_two_runs = mark.hour == 6 and (mark.minute < 20 or mark.minute >= 40)
        return 5 if mark.toordinal() % 8 == 0 and in_two_runs else 0

    _seed(
        mariadb_client,
        _history(61, Numbers(), conditions=Conditions(on_polls=split_morning)),
    )

    ThermalModelJob(mariadb_client).refresh(NOW)

    stored = _thermal_models(mariadb_client)[-1]
    assert stored.heating_starts in FEW_STARTS
    assert stored.warm_up_delay_source == "none"

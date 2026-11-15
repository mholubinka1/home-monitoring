"""The thermal model's steps and Heating Starts, from hand-made polls (ADR-0036)."""

from datetime import datetime, timedelta

from octopus_app.data.model import HourlyWeather
from octopus_app.data.thermal import Poll, build_steps, heating_start_times

MARK = datetime(2026, 11, 17, 12, 0)
POLL = timedelta(minutes=2)
WEATHER = {
    MARK.replace(hour=hour): HourlyWeather(temp=10.0, radiation=0.0)
    for hour in (11, 12, 13)
}


def _polls(first: datetime, last: datetime, temp: float = 18.0) -> list[Poll]:
    polls = []
    at = first
    while at <= last:
        polls.append((at, temp, "OFF", False))
        at += POLL
    return polls


def _steps(polls: list[Poll]) -> int:
    return len(build_steps(polls, WEATHER, MARK, MARK + timedelta(minutes=10), (0,)))


def _without_first_two_polls_of_the_step() -> list[Poll]:
    return [
        poll
        for poll in _polls(MARK - timedelta(minutes=10), MARK + timedelta(minutes=30))
        if poll[0] not in (MARK, MARK + POLL)
    ]


def test_a_step_with_all_its_polls_is_built() -> None:
    polls = _polls(MARK - timedelta(minutes=10), MARK + timedelta(minutes=30))

    assert _steps(polls) == 1


def test_a_step_starts_from_a_reading_3_minutes_59_seconds_after_its_mark() -> None:
    # Given no poll at the mark or 2 minutes after it, then one at 3:59
    polls = _without_first_two_polls_of_the_step()
    polls.append((MARK + timedelta(minutes=3, seconds=59), 18.0, "OFF", False))

    assert _steps(polls) == 1


def test_a_step_does_not_start_from_a_reading_4_minutes_after_its_mark() -> None:
    # Given no poll at the mark or 2 minutes after it; the first is at 4 minutes, and
    # a poll at 9 minutes keeps four polls in the step so only its reading is missing
    polls = _without_first_two_polls_of_the_step()
    polls.append((MARK + timedelta(minutes=9), 18.0, "OFF", False))

    assert _steps(polls) == 0


def test_a_step_with_three_polls_in_its_heating_span_is_left_out() -> None:
    polls = [
        poll
        for poll in _polls(MARK - timedelta(minutes=10), MARK + timedelta(minutes=30))
        if poll[0] not in (MARK + 3 * POLL, MARK + 4 * POLL)
    ]

    assert len(polls) < 20
    assert _steps(polls) == 0


def test_heating_minutes_never_exceed_the_step_when_a_span_holds_extra_polls() -> None:
    # Given a catch-up poll that gives the step 6 polls, all with the heating on
    polls: list[Poll] = [
        (at, 18.0, "ON", False) for at in (MARK + POLL * index for index in range(5))
    ]
    polls += [(MARK + timedelta(minutes=9), 18.0, "ON", False)]
    polls += _polls(MARK - timedelta(minutes=10), MARK - POLL)
    polls += _polls(MARK + timedelta(minutes=10), MARK + timedelta(minutes=30))

    steps = build_steps(polls, WEATHER, MARK, MARK + timedelta(minutes=10), (0,))

    # Then it counts 10 minutes of heating, not 12
    assert steps[0].on_minutes == (10,)


def test_a_step_whose_first_poll_has_no_temperature_is_left_out() -> None:
    polls = _polls(MARK - timedelta(minutes=10), MARK + timedelta(minutes=30))
    polls[5] = (polls[5][0], None, "OFF", False)  # the poll at the mark

    assert polls[5][0] == MARK
    assert _steps(polls) == 0


def test_the_outdoor_temperature_is_interpolated_between_the_hourly_stamps() -> None:
    weather = {
        MARK: HourlyWeather(temp=10.0, radiation=0.0),
        MARK + timedelta(hours=1): HourlyWeather(temp=16.0, radiation=500.0),
    }
    polls = _polls(MARK - timedelta(minutes=10), MARK + timedelta(minutes=50))
    mark = MARK + timedelta(minutes=30)

    steps = build_steps(polls, weather, mark, mark + timedelta(minutes=10), (0,))
    step = steps[0]

    # halfway between 10 C and 16 C, with the radiation of the hour, in kW
    assert step.outdoor == 13.0
    assert step.radiation_kw == 0.5


def _on_polls(*times: datetime) -> list[Poll]:
    return [(at, 18.0, "ON", False) for at in times]


def test_the_heating_coming_on_after_30_quiet_minutes_is_a_heating_start() -> None:
    first = datetime(2026, 11, 17, 6, 0)

    starts = heating_start_times(
        _on_polls(
            first,
            first + POLL,
            first + timedelta(minutes=20),  # 18 minutes after the last on: not a start
            first + timedelta(minutes=50),  # 30 minutes after it: a start
        )
    )

    assert starts == [first, first + timedelta(minutes=50)]


def test_boost_counts_as_the_heating_being_on() -> None:
    at = datetime(2026, 11, 17, 6, 0)

    assert heating_start_times([(at, 18.0, "OFF", True)]) == [at]

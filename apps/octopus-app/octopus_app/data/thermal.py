"""The thermal model: how the room's temperature changes over 10 minutes, fitted
by least squares over 10-minute steps read straight from the thermostat polls
(ADR-0035, ADR-0036). The numbers are per hour.

change = 1/6 * (loss_per_degree * (outdoor - room) + sun_gain * radiation
                + constant_gain)
         + heating_gain * heating minutes / 60
         + carried_gain * minutes of heating in the 10 minutes before / 60

There is no numerical library in this app, so the normal equations are solved in
plain Python. Each step's contribution to the normal equations is summed once per
hour, and a window's equations are the difference of two running totals, so a fit
of any 60 days costs the same."""

import math
from bisect import bisect_left
from collections.abc import Iterable, Sequence
from dataclasses import astuple, dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from itertools import accumulate

from octopus_app.data.heating_intervals import POLL_MINUTES, poll_is_on
from octopus_app.data.model import HourlyWeather

ONE_HOUR = timedelta(hours=1)
MINUTES_PER_HOUR = 60
WATTS_PER_KILOWATT = 1000
STEP_MINUTES = 10
STEP = timedelta(minutes=STEP_MINUTES)
STEPS_PER_HOUR = MINUTES_PER_HOUR // STEP_MINUTES
POLLS_PER_STEP = STEP_MINUTES // POLL_MINUTES
MIN_POLLS_PER_SPAN = 4  # of the 5 a 2-minute poll interval gives in 10 minutes
# A room reading counts as a step's start (or end) when it comes this soon after
# the mark: a couple of polls at hive-app's 2-minute interval.
READING_TOLERANCE = timedelta(minutes=4)
FIT_WINDOW = timedelta(days=60)
MIN_STEPS = 1008  # a week of 10-minute steps
MIN_HEATING_STEPS = 30
MIN_HEATING_MINUTES = 4  # a step with this much heating is a heating step
TERMS = 5
BACKTEST_DAYS = 14
MIN_BACKTEST_DAYS = 5
MAX_MEAN_ERROR = 0.5  # C
# A pivot this small, against the largest entry of the normal equations, means
# the inputs do not vary enough to tell the numbers apart.
PIVOT_TOLERANCE = 1e-12
# The delays searched, in minutes (the Warm-Up Delay, ADR-0036).
MAX_DELAY_MINUTES = 40
DELAY_STEP_MINUTES = 2
SEARCHED_DELAYS = tuple(range(0, MAX_DELAY_MINUTES + 1, DELAY_STEP_MINUTES))
# A delay is learned only when the fit window holds this many Heating Starts: the
# heating coming on after at least this long with none.
MIN_HEATING_STARTS = 10
QUIET_BEFORE_START = timedelta(minutes=30)


class Unusable(StrEnum):
    TOO_FEW_STEPS = "too_few_steps"
    TOO_LITTLE_HEATING = "too_little_heating"
    NO_FIT = "no_fit"
    IMPLAUSIBLE = "implausible"
    TOO_FEW_BACKTEST_DAYS = "too_few_backtest_days"
    BACKTEST_ERROR_TOO_HIGH = "backtest_error_too_high"


@dataclass(frozen=True)
class ThermalNumbers:
    loss_per_degree: float
    heating_gain: float
    carried_gain: float
    sun_gain: float
    constant_gain: float


@dataclass(frozen=True)
class ThermalFit:
    """A fit and how far it can be trusted: the reason is None when it is usable.
    The numbers are None when the equations could not be solved or were not tried."""

    steps_used: int
    heating_steps: int
    numbers: ThermalNumbers | None
    reason: Unusable | None


# One thermostat poll: when, the room temperature, the state and boost.
Poll = tuple[datetime, float | None, str | None, bool | None]


@dataclass(frozen=True)
class Step:
    """One 10-minute step's inputs and outcome. The heating minutes are one per
    searched delay, the heating read from the poll minutes that far earlier."""

    start: datetime  # naive UTC, on a 10-minute mark
    room: float  # at the start, C
    outdoor: float  # at the start, C
    radiation_kw: float
    change: float  # the room's change over the step, C
    on_minutes: tuple[int, ...]
    carried_minutes: tuple[int, ...]  # the 10 minutes before the heating's

    @property
    def gap(self) -> float:
        return self.outdoor - self.room

    def inputs(self, delay_index: int, room: float | None = None) -> list[float]:
        """The five regressors; the room can be replaced to replay a prediction."""
        gap = self.gap if room is None else self.outdoor - room
        return [
            gap / STEPS_PER_HOUR,
            self.on_minutes[delay_index] / MINUTES_PER_HOUR,
            self.carried_minutes[delay_index] / MINUTES_PER_HOUR,
            self.radiation_kw / STEPS_PER_HOUR,
            1.0 / STEPS_PER_HOUR,
        ]


def _is_finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


class _Polls:
    """The thermostat polls, searchable by time."""

    def __init__(self, polls: Iterable[Poll]) -> None:
        self._polls = sorted(polls, key=lambda poll: poll[0])
        self._times = [poll[0] for poll in self._polls]
        self._on_before = list(
            accumulate(
                (poll_is_on(poll[2], poll[3]) for poll in self._polls), initial=0
            )
        )

    def on_in(self, start: datetime, end: datetime) -> tuple[int, int]:
        """The number of polls in [start, end) and how many of them are on."""
        low, high = bisect_left(self._times, start), bisect_left(self._times, end)
        return high - low, self._on_before[high] - self._on_before[low]

    def room_at(self, mark: datetime) -> float | None:
        """The room temperature of the first poll within the tolerance after the
        mark. That poll having no temperature leaves the mark without one; a later
        poll is never used in its place."""
        index = bisect_left(self._times, mark)
        if index == len(self._times) or self._times[index] - mark >= READING_TOLERANCE:
            return None
        temp = self._polls[index][1]
        return temp if _is_finite(temp) else None


def heating_start_times(poll_rows: Iterable[Poll]) -> list[datetime]:
    """When the heating came on after at least 30 minutes with none (a Heating
    Start), oldest first."""
    starts: list[datetime] = []
    last_on: datetime | None = None
    for at, _, state, boost in sorted(poll_rows, key=lambda poll: poll[0]):
        if not poll_is_on(state, boost):
            continue
        if last_on is None or at - last_on >= QUIET_BEFORE_START:
            starts.append(at)
        last_on = at
    return starts


def count_between(times: Sequence[datetime], start: datetime, end: datetime) -> int:
    """How many of the sorted times lie in [start, end)."""
    return bisect_left(times, end) - bisect_left(times, start)


def _interpolated_outdoor(
    weather: dict[datetime, HourlyWeather], mark: datetime
) -> tuple[float, float] | None:
    """The outdoor temperature at the mark, in a straight line between the hourly
    stamps either side, and the radiation (kW/m2) of the hour. An hour's mean
    radiation is stamped at its end (ADR-0029)."""
    hour = mark.replace(minute=0, second=0, microsecond=0)
    before, after = weather.get(hour), weather.get(hour + ONE_HOUR)
    if before is None or after is None or after.radiation is None:
        return None
    fraction = (mark - hour) / ONE_HOUR
    return (
        before.temp + fraction * (after.temp - before.temp),
        after.radiation / WATTS_PER_KILOWATT,
    )


def _heating_minutes(
    polls: _Polls, start: datetime, delays: Sequence[int]
) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    """The minutes the heating was on in the step and in the step before it, read
    from that long before each searched delay; None unless every one of those
    spans has enough polls."""
    on_minutes, carried_minutes = [], []
    for delay in delays:
        begin = start - timedelta(minutes=delay)
        counts = [
            polls.on_in(begin - offset, begin - offset + STEP)
            for offset in (timedelta(0), STEP)
        ]
        if any(count < MIN_POLLS_PER_SPAN for count, _ in counts):
            return None
        # A retried poll can give a span more than five; the heating cannot have
        # been on for longer than the span.
        on_minutes.append(min(STEP_MINUTES, POLL_MINUTES * counts[0][1]))
        carried_minutes.append(min(STEP_MINUTES, POLL_MINUTES * counts[1][1]))
    return tuple(on_minutes), tuple(carried_minutes)


def build_steps(
    poll_rows: Iterable[Poll],
    weather: dict[datetime, HourlyWeather],
    start: datetime,
    end: datetime,
    delays: Sequence[int] = SEARCHED_DELAYS,
) -> list[Step]:
    """The complete steps starting in [start, end) (naive UTC marks). A step needs
    a room reading after its start and after its end, at least 4 of the 5 polls
    in each of the spans its heating is read from (at every searched delay), and
    the weather; one missing any of these is left out."""
    polls = _Polls(poll_rows)
    steps = []
    mark = start
    while mark < end:
        step = _step_at(polls, weather, mark, delays)
        if step is not None:
            steps.append(step)
        mark += STEP
    return steps


def _step_at(
    polls: _Polls,
    weather: dict[datetime, HourlyWeather],
    mark: datetime,
    delays: Sequence[int],
) -> Step | None:
    room_start, room_end = polls.room_at(mark), polls.room_at(mark + STEP)
    outdoor = _interpolated_outdoor(weather, mark)
    if room_start is None or room_end is None or outdoor is None:
        return None
    heating = _heating_minutes(polls, mark, delays)
    if heating is None:
        return None
    return Step(
        start=mark,
        room=room_start,
        outdoor=outdoor[0],
        radiation_kw=outdoor[1],
        change=room_end - room_start,
        on_minutes=heating[0],
        carried_minutes=heating[1],
    )


def _normal_entries(inputs: list[float], change: float) -> list[float]:
    """One step's addition to the sums: the upper triangle of the normal-equation
    matrix, then its right-hand side, then the squared change."""
    entries = [
        inputs[row] * inputs[column]
        for row in range(TERMS)
        for column in range(row, TERMS)
    ]
    entries += [value * change for value in inputs]
    entries.append(change * change)
    return entries


MATRIX_ENTRIES = TERMS * (TERMS + 1) // 2
SUMS = MATRIX_ENTRIES + TERMS + 1  # the matrix, its right-hand side, the squares


@dataclass(frozen=True)
class WindowSums:
    """What a window of steps adds to the normal equations, at one delay."""

    steps: int
    heating_steps: int
    entries: list[float]

    def matrix(self) -> list[list[float]]:
        matrix = [[0.0] * TERMS for _ in range(TERMS)]
        index = 0
        for row in range(TERMS):
            for column in range(row, TERMS):
                matrix[row][column] = matrix[column][row] = self.entries[index]
                index += 1
        return matrix

    def target(self) -> list[float]:
        return self.entries[MATRIX_ENTRIES : MATRIX_ENTRIES + TERMS]

    def squared_change(self) -> float:
        return self.entries[-1]


class StepLedger:
    """The steps, with the running totals of what each adds to the normal
    equations at each searched delay, by the hour they start in."""

    def __init__(self, steps: list[Step], delays: Sequence[int] = SEARCHED_DELAYS):
        self.delays = tuple(delays)
        self.steps = steps
        self._starts = [step.start for step in steps]
        self._hours = sorted({step.start.replace(minute=0) for step in steps})
        hour_index = {hour: index for index, hour in enumerate(self._hours)}
        # per delay, per hour: [steps, heating steps, *entries]
        per_hour = [[[0.0] * (2 + SUMS) for _ in self._hours] for _ in self.delays]
        for step in steps:
            index = hour_index[step.start.replace(minute=0)]
            for delay_index in range(len(self.delays)):
                row = per_hour[delay_index][index]
                row[0] += 1
                row[1] += step.on_minutes[delay_index] >= MIN_HEATING_MINUTES
                entries = _normal_entries(step.inputs(delay_index), step.change)
                for position, value in enumerate(entries):
                    row[2 + position] += value
        self._totals = [
            [[0.0] * (2 + SUMS), *accumulate(rows, _add_rows)] for rows in per_hour
        ]

    def window(self, delay_index: int, start: datetime, end: datetime) -> WindowSums:
        """The sums over the steps starting in [start, end), whole hours."""
        low = bisect_left(self._hours, start)
        high = bisect_left(self._hours, end)
        totals = self._totals[delay_index]
        row = [after - before for before, after in zip(totals[low], totals[high])]
        return WindowSums(int(row[0]), int(row[1]), row[2:])

    def steps_between(self, start: datetime, end: datetime) -> list[Step]:
        low, high = bisect_left(self._starts, start), bisect_left(self._starts, end)
        return self.steps[low:high]


def _add_rows(total: list[float], row: list[float]) -> list[float]:
    return [a + b for a, b in zip(total, row)]


def _largest_in_column(rows: list[list[float]], column: int) -> int:
    """The row, from `column` down, with the largest value in the column."""
    return max(range(column, TERMS), key=lambda row: abs(rows[row][column]))


def _solve(matrix: list[list[float]], target: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; None when singular."""
    rows = [[*row, value] for row, value in zip(matrix, target, strict=True)]
    scale = max(abs(value) for row in matrix for value in row)
    for column in range(TERMS):
        pivot = _largest_in_column(rows, column)
        if abs(rows[pivot][column]) <= PIVOT_TOLERANCE * scale:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for below in range(column + 1, TERMS):
            factor = rows[below][column] / rows[column][column]
            rows[below] = [
                a - factor * b for a, b in zip(rows[below], rows[column], strict=True)
            ]
    solution = [0.0] * TERMS
    for row in reversed(range(TERMS)):
        known = sum(rows[row][c] * solution[c] for c in range(row + 1, TERMS))
        solution[row] = (rows[row][TERMS] - known) / rows[row][row]
    return solution


def fit_window(sums: WindowSums) -> ThermalFit:
    """The least-squares numbers over a window's steps, and the first usability
    check (in the order of ADR-0035) that they fail."""
    if sums.steps < MIN_STEPS:
        return ThermalFit(sums.steps, sums.heating_steps, None, Unusable.TOO_FEW_STEPS)
    if sums.heating_steps < MIN_HEATING_STEPS:
        return ThermalFit(
            sums.steps, sums.heating_steps, None, Unusable.TOO_LITTLE_HEATING
        )
    solution = _solve(sums.matrix(), sums.target())
    if solution is None:
        return ThermalFit(sums.steps, sums.heating_steps, None, Unusable.NO_FIT)
    numbers = ThermalNumbers(*solution)
    plausible = numbers.loss_per_degree > 0 and numbers.heating_gain > 0
    reason = None if plausible else Unusable.IMPLAUSIBLE
    return ThermalFit(sums.steps, sums.heating_steps, numbers, reason)


def _squared_misses(numbers: ThermalNumbers, sums: WindowSums) -> float:
    """The sum of the squared misses of the numbers over a window's steps, from its
    sums: change squared less twice its product with the inputs, plus their square."""
    beta = list(astuple(numbers))
    matrix, target = sums.matrix(), sums.target()
    cross = sum(b * t for b, t in zip(beta, target, strict=True))
    quadratic = sum(
        beta[row] * matrix[row][column] * beta[column]
        for row in range(TERMS)
        for column in range(TERMS)
    )
    return sums.squared_change() - 2 * cross + quadratic


def search_delay(
    ledger: StepLedger, start: datetime, end: datetime
) -> tuple[int, ThermalFit] | None:
    """The searched delay, as an index, whose fit misses the readings least over a
    window (the smaller on a tie), with that fit. The best fit is kept even when its
    numbers make no physical sense: a worse delay is never chosen to hide an
    implausible model. None when no delay can be fitted (too few steps, too little
    heating or no fit at any)."""
    best: tuple[int, ThermalFit] | None = None
    best_misses = math.inf
    for index in range(len(ledger.delays)):
        sums = ledger.window(index, start, end)
        fit = fit_window(sums)
        if fit.numbers is None:
            continue
        misses = _squared_misses(fit.numbers, sums)
        if misses < best_misses:
            best, best_misses = (index, fit), misses
    return best


@dataclass(frozen=True)
class DayWindow:
    """One local day, [start, end) in naive UTC: 23, 24 or 25 hours."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class Backtest:
    """How far replays strayed from the thermostat. The errors are None when no
    day could be replayed."""

    days: int
    mean_error: float | None
    worst_error: float | None


def _predicted_change(
    numbers: ThermalNumbers, step: Step, delay_index: int, room: float
) -> float:
    return sum(
        number * value
        for number, value in zip(
            astuple(numbers), step.inputs(delay_index, room), strict=True
        )
    )


def replay(
    numbers: ThermalNumbers, steps: Sequence[Step], delay_index: int = 0
) -> list[float]:
    """The absolute error of each step of a replay that starts from the first
    step's room and feeds its own prediction forward. Each step's error is the
    predicted room at its end against the room the thermostat recorded then."""
    room = steps[0].room
    errors = []
    for step in steps:
        room += _predicted_change(numbers, step, delay_index, room)
        errors.append(abs(room - (step.room + step.change)))
    return errors


def _replay_day(
    ledger: StepLedger,
    day: DayWindow,
    delay_index: int,
    heating_starts: Sequence[datetime],
) -> list[float] | None:
    """The errors of the day replayed by a model fitted on the 60 days before it,
    with that model's own delay (searched when its window holds enough Heating
    Starts, else the delay of the fit under test), or None when the day has a gap
    or that model fails the first four checks."""
    steps = ledger.steps_between(day.start, day.end)
    if len(steps) != (day.end - day.start) // STEP:
        return None
    window_start = day.start - FIT_WINDOW
    own_index = delay_index
    if count_between(heating_starts, window_start, day.start) >= MIN_HEATING_STARTS:
        searched = search_delay(ledger, window_start, day.start)
        if searched is None:
            return None
        own_index = searched[0]
    earlier = fit_window(ledger.window(own_index, window_start, day.start))
    if earlier.numbers is None or earlier.reason is not None:
        return None
    return replay(earlier.numbers, steps, own_index)


def backtest(
    ledger: StepLedger,
    days: Sequence[DayWindow],
    delay_index: int,
    heating_starts: Sequence[datetime],
) -> Backtest:
    errors: list[float] = []
    replayed = 0
    for day in days:
        day_errors = _replay_day(ledger, day, delay_index, heating_starts)
        if day_errors is not None:
            replayed += 1
            errors += day_errors
    if not errors:
        return Backtest(replayed, None, None)
    return Backtest(replayed, sum(errors) / len(errors), max(errors))


def with_backtest(fit: ThermalFit, result: Backtest) -> ThermalFit:
    """The fit, made unusable by the backtest when it fails checks 5 or 6."""
    if fit.reason is not None:
        return fit
    if result.mean_error is None or result.days < MIN_BACKTEST_DAYS:
        return replace(fit, reason=Unusable.TOO_FEW_BACKTEST_DAYS)
    if result.mean_error > MAX_MEAN_ERROR:
        return replace(fit, reason=Unusable.BACKTEST_ERROR_TOO_HIGH)
    return fit

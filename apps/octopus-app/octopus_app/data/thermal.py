"""The thermal model: how the room's temperature changes over an hour, fitted by
least squares over hourly steps (ADR-0035).

change = loss_per_degree * (outdoor - room) + heating_gain * heating
         + carried_gain * previous heating + sun_gain * radiation + constant_gain

There is no numerical library in this app, so the normal equations are solved in
plain Python."""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import astuple, dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from octopus_app.data.model import HeatingHourRecord, HourlyWeather

ONE_HOUR = timedelta(hours=1)
# A room reading counts as an hour's start when it comes this soon after the hour
# begins: a few polls at hive-app's 2-minute interval (ADR-0033).
START_TOLERANCE = timedelta(minutes=10)
MINUTES_PER_HOUR = 60
WATTS_PER_KILOWATT = 1000
FIT_WINDOW = timedelta(days=60)
MIN_STEPS = 168  # a week of hours
MIN_HEATING_STEPS = 10
MIN_HEATING_MINUTES = 10  # an hour with this much heating is a heating step
TERMS = 5
BACKTEST_DAYS = 14
MIN_BACKTEST_DAYS = 5
MAX_MEAN_ERROR = 0.5  # C
# A pivot this small, against the largest entry of the normal equations, means
# the inputs do not vary enough to tell the numbers apart.
PIVOT_TOLERANCE = 1e-12


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


@dataclass(frozen=True)
class Step:
    """One hour's inputs and outcome."""

    room: float  # at the hour's start, C
    outdoor: float  # at the hour's start, C
    on_minutes: int
    previous_on_minutes: int
    radiation_kw: float
    change: float  # the room's change over the hour, C

    @property
    def gap(self) -> float:
        return self.outdoor - self.room


@dataclass(frozen=True)
class HourlyEvidence:
    """What the readings say about each hour, by hour start (naive UTC)."""

    room_starts: Mapping[datetime, float]
    on_minutes: Mapping[datetime, int]
    weather: Mapping[datetime, HourlyWeather]

    @classmethod
    def from_readings(
        cls,
        room_readings: Iterable[tuple[datetime, float | None]],
        heating_hours: Iterable[HeatingHourRecord],
        weather: Mapping[datetime, HourlyWeather],
    ) -> "HourlyEvidence":
        return cls(
            room_starts=_room_start_temperatures(room_readings),
            on_minutes={row.hour_start: row.on_minutes for row in heating_hours},
            weather=weather,
        )


def _hour_of(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _room_start_temperatures(
    readings: Iterable[tuple[datetime, float | None]],
) -> dict[datetime, float]:
    """Per hour start, the temperature of the first reading within the start
    tolerance. That reading having no temperature leaves the hour without one;
    a later reading is never used in its place."""
    starts: dict[datetime, float] = {}
    seen: set[datetime] = set()
    for at, temp in sorted(readings, key=lambda reading: reading[0]):
        hour = _hour_of(at)
        if at - hour >= START_TOLERANCE or hour in seen:
            continue
        seen.add(hour)
        if temp is not None and math.isfinite(temp):
            starts[hour] = temp
    return starts


def _pair[T](
    values: Mapping[datetime, T], first: datetime, second: datetime
) -> tuple[T, T] | None:
    a, b = values.get(first), values.get(second)
    return None if a is None or b is None else (a, b)


def _step_at(hour: datetime, evidence: HourlyEvidence) -> Step | None:
    rooms = _pair(evidence.room_starts, hour, hour + ONE_HOUR)
    heating = _pair(evidence.on_minutes, hour, hour - ONE_HOUR)
    # Open-Meteo stamps an hour's mean radiation at its end (ADR-0029).
    weather = _pair(evidence.weather, hour, hour + ONE_HOUR)
    if rooms is None or heating is None or weather is None:
        return None
    outdoor, radiation = weather[0].temp, weather[1].radiation
    if radiation is None:
        return None
    return Step(
        room=rooms[0],
        outdoor=outdoor,
        on_minutes=heating[0],
        previous_on_minutes=heating[1],
        radiation_kw=radiation / WATTS_PER_KILOWATT,
        change=rooms[1] - rooms[0],
    )


def hourly_steps(
    evidence: HourlyEvidence, start: datetime, end: datetime
) -> list[Step]:
    """The steps of the hours starting in [start, end) (naive UTC)."""
    steps = []
    hour = start
    while hour < end:
        step = _step_at(hour, evidence)
        if step is not None:
            steps.append(step)
        hour += ONE_HOUR
    return steps


def _inputs(step: Step) -> list[float]:
    return [
        step.gap,
        step.on_minutes / MINUTES_PER_HOUR,
        step.previous_on_minutes / MINUTES_PER_HOUR,
        step.radiation_kw,
        1.0,
    ]


def _normal_equations(steps: list[Step]) -> tuple[list[list[float]], list[float]]:
    matrix = [[0.0] * TERMS for _ in range(TERMS)]
    target = [0.0] * TERMS
    for step in steps:
        inputs = _inputs(step)
        for row in range(TERMS):
            target[row] += inputs[row] * step.change
            for column in range(TERMS):
                matrix[row][column] += inputs[row] * inputs[column]
    return matrix, target


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


def fit_thermal_model(steps: list[Step]) -> ThermalFit:
    """The least-squares numbers over the steps, and the first usability check
    (in the order of ADR-0035) that they fail."""
    heating_steps = sum(1 for s in steps if s.on_minutes >= MIN_HEATING_MINUTES)
    if len(steps) < MIN_STEPS:
        return ThermalFit(len(steps), heating_steps, None, Unusable.TOO_FEW_STEPS)
    if heating_steps < MIN_HEATING_STEPS:
        return ThermalFit(len(steps), heating_steps, None, Unusable.TOO_LITTLE_HEATING)
    solution = _solve(*_normal_equations(steps))
    if solution is None:
        return ThermalFit(len(steps), heating_steps, None, Unusable.NO_FIT)
    numbers = ThermalNumbers(*solution)
    plausible = numbers.loss_per_degree > 0 and numbers.heating_gain > 0
    reason = None if plausible else Unusable.IMPLAUSIBLE
    return ThermalFit(len(steps), heating_steps, numbers, reason)


@dataclass(frozen=True)
class DayHours:
    """The hours of one local day, [start, end) in naive UTC: 23, 24 or 25."""

    start: datetime
    end: datetime


@dataclass(frozen=True)
class Backtest:
    """How far replays strayed from the thermostat. The errors are None when no
    day could be replayed."""

    days: int
    mean_error: float | None
    worst_error: float | None


def _predicted_change(numbers: ThermalNumbers, step: Step) -> float:
    return sum(
        number * value
        for number, value in zip(astuple(numbers), _inputs(step), strict=True)
    )


def replay(numbers: ThermalNumbers, steps: Sequence[Step]) -> list[float]:
    """The absolute error of each hour of a replay that starts from the first
    step's room and feeds its own prediction forward. Each hour's error is the
    predicted room at its end against the room the thermostat recorded then."""
    room = steps[0].room
    errors = []
    for step in steps:
        room += _predicted_change(numbers, replace(step, room=room))
        errors.append(abs(room - (step.room + step.change)))
    return errors


def _replay_day(evidence: HourlyEvidence, day: DayHours) -> list[float] | None:
    """The errors of the day replayed by a model fitted on the 60 days before it,
    or None when the day has a gap or that model fails the first four checks."""
    steps = hourly_steps(evidence, day.start, day.end)
    if len(steps) != (day.end - day.start) // ONE_HOUR:
        return None
    earlier = fit_thermal_model(
        hourly_steps(evidence, day.start - FIT_WINDOW, day.start)
    )
    if earlier.numbers is None or earlier.reason is not None:
        return None
    return replay(earlier.numbers, steps)


def backtest(evidence: HourlyEvidence, days: Sequence[DayHours]) -> Backtest:
    errors: list[float] = []
    replayed = 0
    for day in days:
        day_errors = _replay_day(evidence, day)
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

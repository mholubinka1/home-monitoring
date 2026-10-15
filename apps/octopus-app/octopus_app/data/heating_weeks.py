"""Pure weekly summary of the heating model: no database access."""

import math
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import Enum

from octopus_app.data.heating_model import BaselinePoint

# Model parameters, all in one place.
MIN_WEEK_DAYS = 5  # counted days a week needs to be shown
MIN_WEEK_DEGREE_DAYS_PER_DAY = 1.5  # a milder week says nothing about efficiency
MIN_WEEK_HEATING_GAS_PER_DAY = 0.5  # kWh a day; less means the heating was off


class WeekStatus(Enum):
    SHOWN = "shown"
    TOO_FEW_DAYS = "too few days"
    TOO_MILD = "too mild"
    HEATING_OFF = "heating off"


@dataclass(frozen=True)
class WeekRow:
    """One Monday-to-Sunday week. The averages are per counted day and None
    when the week has no counted day; efficiency is heating gas per
    degree-day, None when there is no counted day or no degree-day."""

    week_start: date
    complete_days: int
    avg_gas: float | None
    avg_degree_days: float | None
    avg_baseload: float | None
    avg_heating_gas: float | None
    efficiency: float | None
    status: WeekStatus


def _status(week: WeekRow) -> WeekStatus:
    if week.complete_days < MIN_WEEK_DAYS:
        return WeekStatus.TOO_FEW_DAYS
    if (week.avg_degree_days or 0.0) < MIN_WEEK_DEGREE_DAYS_PER_DAY:
        return WeekStatus.TOO_MILD
    if (week.avg_heating_gas or 0.0) < MIN_WEEK_HEATING_GAS_PER_DAY:
        return WeekStatus.HEATING_OFF
    return WeekStatus.SHOWN


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


@dataclass(frozen=True)
class _Inputs:
    gas: Mapping[date, float]
    effective: Mapping[date, float]
    baseload: Mapping[date, BaselinePoint]
    threshold: float


def _week_row(week_start: date, counted: list[date], inputs: _Inputs) -> WeekRow:
    avg_degree_days = _mean(
        [max(0.0, inputs.threshold - inputs.effective[d]) for d in counted]
    )
    avg_heating_gas = _mean([inputs.gas[d] - inputs.baseload[d].kwh for d in counted])
    row = WeekRow(
        week_start=week_start,
        complete_days=len(counted),
        avg_gas=_mean([inputs.gas[d] for d in counted]),
        avg_degree_days=avg_degree_days,
        avg_baseload=_mean([inputs.baseload[d].kwh for d in counted]),
        avg_heating_gas=avg_heating_gas,
        efficiency=(
            avg_heating_gas / avg_degree_days
            if avg_heating_gas is not None and avg_degree_days
            else None
        ),
        status=WeekStatus.SHOWN,
    )
    return replace(row, status=_status(row))


def weekly_rows(
    gas: Mapping[date, float],
    effective: Mapping[date, float],
    baseload: Mapping[date, BaselinePoint],
    away_days: AbstractSet[date],
    threshold: float,
) -> list[WeekRow]:
    """One row per Monday-start week that holds at least one day of `gas`,
    oldest first. `gas` holds only usable (complete) days.

    A day counts when it is in gas, effective and baseload and is not an away
    day; every average is over counted days only, and heating gas is the day's
    gas less that day's own moving baseload. Status, in order: fewer than
    MIN_WEEK_DAYS counted days is TOO_FEW_DAYS, under
    MIN_WEEK_DEGREE_DAYS_PER_DAY average degree-days is TOO_MILD, under
    MIN_WEEK_HEATING_GAS_PER_DAY average heating gas is HEATING_OFF, else SHOWN.

    Raises ValueError when the threshold or any gas or effective temperature
    is not finite."""
    if not (
        math.isfinite(threshold)
        and all(math.isfinite(value) for value in gas.values())
        and all(math.isfinite(value) for value in effective.values())
    ):
        raise ValueError("gas, effective temperatures and threshold must be finite")
    weeks: dict[date, list[date]] = {}
    for day in sorted(gas):
        week_days = weeks.setdefault(day - timedelta(days=day.weekday()), [])
        if day in effective and day in baseload and day not in away_days:
            week_days.append(day)
    inputs = _Inputs(gas, effective, baseload, threshold)
    return [
        _week_row(start, counted, inputs) for start, counted in sorted(weeks.items())
    ]

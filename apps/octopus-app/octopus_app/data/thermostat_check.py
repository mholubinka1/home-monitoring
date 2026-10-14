"""Pure thermostat check: classify days from heating-status readings and
cross-check the gas-derived threshold. No database access."""

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from itertools import pairwise

from common.local_day import to_local_date

# Check parameters, all in one place.
POLL_MINUTES = 2  # readings are this far apart
EXPECTED_READINGS = 720  # readings in a full day
MIN_READING_SHARE = 0.9  # a day needs this share of the expected readings
MIN_READINGS = round(MIN_READING_SHARE * EXPECTED_READINGS)  # 648
MIN_HEATING_MINUTES = 30  # demand needed to make a heating day
DEMAND_STATE = "ON"  # heating_status.state is exactly "ON" or "OFF" (hive-app)
LABEL_TOLERANCE = 1.5  # thermostat and gas thresholds may differ by this (C)
MIN_DAYS_EACH_CLASS = 15  # heating and non-heating days needed to confirm
GAP_DECIMALS = 6  # float noise in a threshold gap (16.1 - 14.6) is far below this


@dataclass(frozen=True)
class HeatingReading:
    """One heating-status poll: the thermostat state and whether boost was on."""

    polled_at: datetime  # timezone-aware
    state: str
    boost_active: bool


def classify_days(readings: Iterable[HeatingReading]) -> dict[date, bool]:
    """Heating (True) or non-heating (False) for each local day with enough
    readings; a day with too few is absent. A heating day has at least
    MIN_HEATING_MINUTES of thermostat demand, ignoring boost."""
    total: defaultdict[date, int] = defaultdict(int)
    demand: defaultdict[date, int] = defaultdict(int)
    for reading in readings:
        day = to_local_date(reading.polled_at)
        total[day] += 1
        if reading.state == DEMAND_STATE and not reading.boost_active:
            demand[day] += 1
    return {
        day: demand[day] * POLL_MINUTES >= MIN_HEATING_MINUTES
        for day, count in total.items()
        if count >= MIN_READINGS
    }


@dataclass(frozen=True)
class ThermostatCheck:
    """The thermostat-side threshold and the days it was drawn from."""

    threshold: float
    heating_days: int
    non_heating_days: int


def thermostat_threshold(
    day_flags: Mapping[date, bool], effective: Mapping[date, float]
) -> ThermostatCheck | None:
    """The effective temperature that best separates heating days (expected
    below the cut-off) from non-heating days (at/above it), over the days in
    both mappings; a day with a non-finite temperature is skipped. Candidate
    cuts are midpoints between consecutive distinct temperatures; the fewest
    misclassified days wins, and equally good cuts give the midpoint between
    the smallest and largest. None when either kind of day is missing, or all
    the days share one temperature."""
    days = [
        day for day in day_flags if day in effective and math.isfinite(effective[day])
    ]
    heating = sorted(effective[day] for day in days if day_flags[day])
    non_heating = sorted(effective[day] for day in days if not day_flags[day])
    if not heating or not non_heating:
        return None
    temperatures = sorted(set(heating + non_heating))
    cuts = [(a + b) / 2 for a, b in pairwise(temperatures)]
    if not cuts:  # every day at one temperature: nothing to separate them by
        return None

    def errors(cut: float) -> int:
        return sum(t >= cut for t in heating) + sum(t < cut for t in non_heating)

    scored = [(errors(cut), cut) for cut in cuts]
    fewest = min(count for count, _ in scored)
    best = [cut for count, cut in scored if count == fewest]
    return ThermostatCheck(
        threshold=(min(best) + max(best)) / 2,
        heating_days=len(heating),
        non_heating_days=len(non_heating),
    )


class ModelLabel(Enum):
    """How far the gas-derived model is backed by the thermostat."""

    ESTIMATED = "estimated"
    CONFIRMED = "confirmed"


def label_model(
    gas_threshold: float | None, check: ThermostatCheck | None
) -> ModelLabel:
    """CONFIRMED when the thermostat threshold is within LABEL_TOLERANCE of the
    gas threshold and both kinds of day have at least MIN_DAYS_EACH_CLASS;
    otherwise ESTIMATED."""
    if (
        check is not None
        and gas_threshold is not None
        and round(abs(check.threshold - gas_threshold), GAP_DECIMALS) <= LABEL_TOLERANCE
        and check.heating_days >= MIN_DAYS_EACH_CLASS
        and check.non_heating_days >= MIN_DAYS_EACH_CLASS
    ):
        return ModelLabel.CONFIRMED
    return ModelLabel.ESTIMATED

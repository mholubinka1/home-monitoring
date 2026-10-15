from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

VOLUME_CORRECTION = Decimal("1.02264")
AVERAGE_CALORIFIC_VALUE = Decimal("39.5")
TO_KWH_DIVISOR = Decimal("3.6")


class Energy(Enum):
    electricity = 0
    gas = 1


def as_energy_char(energy: Energy) -> str:
    match (energy):
        case Energy.electricity:
            return "E"
        case Energy.gas:
            return "G"


def energy_from_char(char: str) -> Energy:
    match (char):
        case "E":
            return Energy.electricity
        case "G":
            return Energy.gas
        case _:
            raise ValueError(f"Unknown energy char: {char!r}")


class Unit(Enum):
    kwh = 0
    m3 = 1


@dataclass
class Consumption:
    raw: Decimal
    est_kwh: Decimal
    unit: Unit  # Electricity: kwh, Gas: m^3
    start: datetime
    end: datetime


@dataclass
class ConsumptionSummary:
    energy: Energy
    date: date
    total_kwh: Decimal
    half_hour_count: int | None = None


@dataclass
class DailyCostSummary:
    date: date
    total_kwh: Decimal
    day_cost_gbp: Decimal
    is_gap_filled: bool = False
    rates_estimated: bool = False


@dataclass
class CostForecast:
    billing_period_start: date
    billing_period_end: date
    actual_cost_to_date: Decimal
    projected_total_cost: Decimal
    computed_at: datetime
    energy: Energy
    rates_estimated: bool = False
    estimated_days: str | None = None


@dataclass(frozen=True)
class HeatingModelRecord:  # pylint: disable=too-many-instance-attributes
    """One heating-model refit as stored. The numbers are None when no fit
    could be made."""

    fitted_at: datetime  # timezone-aware UTC
    window_start: date
    window_end: date
    usable: bool
    unusable_reason: str | None
    days_used: int | None
    threshold: float | None
    upper_threshold: float | None
    slope: float | None
    weight: float | None
    r_squared: float | None
    baseload: float | None
    baseload_measured_days: int | None
    baseload_interpolated_days: int | None
    baseload_held_days: int | None
    noise_base: float | None
    noise_per_degree_day: float | None
    noise_floor: float | None
    thermostat_threshold: float | None
    heating_days: int | None
    non_heating_days: int | None
    label: str  # 'estimated' or 'confirmed'


@dataclass(frozen=True)
class StoredHeatingModel:
    id: int
    model: HeatingModelRecord


@dataclass(frozen=True)
class HeatingDayVerdictRecord:  # pylint: disable=too-many-instance-attributes
    """One day's heating verdict as stored: gas against what the model expects."""

    date: date
    mean_temp: float | None
    effective_temp: float | None
    actual_gas: float
    expected_gas: float
    baseload: float
    baseload_kind: str  # 'measured', 'interpolated' or 'held'
    needed: bool
    away: bool
    expected_heating: float
    normal_variation: float
    possible: float
    clear: float
    margin: float


@dataclass(frozen=True)
class HeatingForecastRecord:
    """One future day's projected gas as stored."""

    date: date
    forecast_mean: float
    effective_temperature: float
    expected_gas: float


def get_raw_unit(energy: Energy) -> Unit:
    match (energy):
        case Energy.gas:
            return Unit.m3
        case Energy.electricity:
            return Unit.kwh
        case _:
            raise NotImplementedError(f"Unit unknown for energy of type: {energy}")


def to_estimated_kwh(energy: Energy, raw: Decimal) -> Decimal:
    if energy == Energy.electricity:
        return raw
    if energy == Energy.gas:
        return (raw * VOLUME_CORRECTION * AVERAGE_CALORIFIC_VALUE) / TO_KWH_DIVISOR

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
    match energy:
        case Energy.electricity:
            return "E"
        case Energy.gas:
            return "G"


def energy_from_char(char: str) -> Energy:
    match char:
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
    slope: float | None
    weight: float | None
    r_squared: float | None
    baseload: float | None
    baseload_measured_days: int | None
    baseload_interpolated_days: int | None
    baseload_held_days: int | None


@dataclass(frozen=True)
class StoredHeatingModel:
    id: int
    model: HeatingModelRecord


@dataclass(frozen=True)
class ThermalModelRecord:  # pylint: disable=too-many-instance-attributes
    """One thermal-model fit as stored (ADR-0035). The numbers are None when no
    fit could be made, and the backtest figures until a backtest has run."""

    fitted_at: datetime  # timezone-aware UTC
    window_start: date
    window_end: date
    usable: bool
    unusable_reason: str | None
    steps_used: int
    heating_steps: int
    loss_per_degree: float | None
    heating_gain: float | None
    carried_gain: float | None
    sun_gain: float | None
    constant_gain: float | None
    backtest_days: int | None
    backtest_mean_error: float | None
    backtest_worst_error: float | None


@dataclass(frozen=True)
class StoredThermalModel:
    id: int
    model: ThermalModelRecord


@dataclass(frozen=True)
class HourlyWeather:
    """One hour stamp's outdoor temperature (C) and mean radiation (W/m2)."""

    temp: float
    radiation: float | None


@dataclass(frozen=True)
class HeatingHourRecord:  # pylint: disable=too-many-instance-attributes
    """One completed hour of heating as stored (naive UTC stamps)."""

    hour_start: datetime
    poll_count: int
    on_minutes: int
    measured_kwh: float | None
    heating_kwh: float | None
    source: str
    unit_rate: float | None
    cost_gbp: float | None
    computed_at: datetime


@dataclass(frozen=True)
class ComfortDayRecord:
    """One local day's comfort figures as stored (ADR-0034)."""

    date: date
    comfort_temp: float | None
    boost_starts: int
    counted_hours: str
    minutes_below: int | None
    computed_at: datetime


@dataclass(frozen=True)
class HeatingForecastRecord:
    """One future day's projected gas as stored."""

    date: date
    forecast_mean: float
    effective_temp: float
    expected_gas: float


def get_raw_unit(energy: Energy) -> Unit:
    match energy:
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

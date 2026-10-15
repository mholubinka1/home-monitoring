from typing import ClassVar

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    false,
)
from sqlalchemy.dialects.mysql import DECIMAL, DOUBLE

from common.mariadb.model import SQLBase, job_run

# A 64-bit float returned as a Python float. SQLAlchemy's Float is a 32-bit
# FLOAT on MariaDB, which would store a blend weight of 0.8 as 0.800000011920929.
_DOUBLE = DOUBLE(asdecimal=False)

__all__ = ["SQLBase", "job_run"]


class consumption(SQLBase):
    __tablename__ = "consumption"
    __table_args__: ClassVar[tuple[Index, dict[str, str]]] = (
        Index("ix_consumption_energy_period_from", "energy", "period_from"),
        {"schema": "octopus"},
    )

    id = Column(String(50), primary_key=True)
    energy = Column(String(1))
    period_from = Column(DateTime, nullable=False)
    period_to = Column(DateTime, nullable=False)
    raw_value = Column(DECIMAL(8, 5, unsigned=True), nullable=False)
    unit = Column(String(5))
    est_kwh = Column(DECIMAL(8, 5, unsigned=True), nullable=False)


class agreement(SQLBase):
    __tablename__ = "agreement"
    __table_args__: ClassVar[tuple[Index, dict[str, str]]] = (
        Index(
            "ix_agreement_energy_valid_from_valid_to",
            "energy",
            "valid_from",
            "valid_to",
        ),
        {"schema": "octopus"},
    )

    id = Column(String(50), primary_key=True)
    energy = Column(String(1), nullable=False)
    product_code = Column(String(50), nullable=False)
    tariff_code = Column(String(50), nullable=False)
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime)


class product(SQLBase):
    __tablename__ = "product"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    product_code = Column(String(50), primary_key=True)
    display_name = Column(String(200))
    direction = Column(String(10))


class product_rate(SQLBase):
    __tablename__ = "product_rate"
    __table_args__: ClassVar[tuple[Index, dict[str, str]]] = (
        Index(
            "ix_product_rate_product_code_region_valid_from_valid_to",
            "product_code",
            "region",
            "valid_from",
            "valid_to",
        ),
        {"schema": "octopus"},
    )

    id = Column(String(70), primary_key=True)
    product_code = Column(String(50), nullable=False)
    region = Column(String(1), nullable=False)
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime)
    unit_rate = Column(Numeric(9, 6), nullable=False)
    standing_charge = Column(Numeric(9, 6), nullable=False)


class daily_consumption_summary(SQLBase):
    __tablename__ = "daily_consumption_summary"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    energy = Column(String(1), primary_key=True)
    date = Column(Date, primary_key=True)
    total_kwh = Column(DECIMAL(8, 5, unsigned=True), nullable=False)
    half_hour_count = Column(Integer, nullable=True)


class agile_forecast(SQLBase):
    __tablename__ = "agile_forecast"
    __table_args__: ClassVar[tuple[Index, dict[str, str]]] = (
        Index("ix_agile_forecast_region_period_from", "region", "period_from"),
        {"schema": "octopus"},
    )

    id = Column(String(70), primary_key=True)
    region = Column(String(1), nullable=False)
    period_from = Column(DateTime, nullable=False)
    period_to = Column(DateTime, nullable=False)
    forecast_unit_rate = Column(Numeric(9, 6), nullable=False)
    fetched_at = Column(DateTime, nullable=False)


class cost_forecast(SQLBase):
    __tablename__ = "cost_forecast"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    billing_period_start = Column(Date, nullable=False)
    billing_period_end = Column(Date, nullable=False)
    actual_cost_to_date = Column(Numeric(9, 2), nullable=False)
    projected_total_cost = Column(Numeric(9, 2), nullable=False)
    computed_at = Column(DateTime, nullable=False)
    # Nullable, not nullable=False -- per ADR-0005, Schema Sync's additive
    # ADD COLUMN would be rejected outright by MariaDB on this table's
    # existing rows if declared NOT NULL with no server_default. The app
    # itself always writes a real value (write_cost_forecast always passes
    # one), so this only ever reads NULL on pre-migration rows until the
    # manual backfill runs -- tightening to NOT NULL afterward is a
    # deliberate manual step, out of scope for the automated tool.
    energy = Column(String(1))
    # server_default so Schema Sync's additive ADD COLUMN is accepted on a
    # table that already has rows (see ADR-0005); those rows read as "not
    # estimated".
    rates_estimated = Column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    estimated_days = Column(Text)


class heating_model(SQLBase):
    __tablename__ = "heating_model"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(Integer, primary_key=True, autoincrement=True)
    fitted_at = Column(DateTime, nullable=False)
    window_start = Column(Date, nullable=False)
    window_end = Column(Date, nullable=False)
    usable = Column(Boolean, nullable=False)
    unusable_reason = Column(String(100))
    days_used = Column(Integer)
    threshold = Column(_DOUBLE)
    upper_threshold = Column(_DOUBLE)
    slope = Column(_DOUBLE)
    weight = Column(_DOUBLE)
    r_squared = Column(_DOUBLE)
    baseload = Column(_DOUBLE)
    baseload_measured_days = Column(Integer)
    baseload_interpolated_days = Column(Integer)
    baseload_held_days = Column(Integer)
    noise_base = Column(_DOUBLE)
    noise_per_degree_day = Column(_DOUBLE)
    noise_floor = Column(_DOUBLE)
    thermostat_threshold = Column(_DOUBLE)
    heating_days = Column(Integer)
    non_heating_days = Column(Integer)
    label = Column(String(20), nullable=False)


class heating_day_verdict(SQLBase):
    __tablename__ = "heating_day_verdict"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    # One row per day, wholly replaced each time the verdicts are recomputed.
    date = Column(Date, primary_key=True)
    model_id = Column(Integer, nullable=False)
    mean_temp = Column(_DOUBLE)
    effective_temp = Column(_DOUBLE)
    actual_gas = Column(_DOUBLE, nullable=False)
    expected_gas = Column(_DOUBLE, nullable=False)
    baseload = Column(_DOUBLE, nullable=False)
    baseload_kind = Column(String(20), nullable=False)
    needed = Column(Boolean, nullable=False)
    away = Column(Boolean, nullable=False)
    expected_heating = Column(_DOUBLE, nullable=False)
    normal_variation = Column(_DOUBLE, nullable=False)
    possible = Column(_DOUBLE, nullable=False)
    clear = Column(_DOUBLE, nullable=False)
    margin = Column(_DOUBLE, nullable=False)


class heating_forecast_day(SQLBase):
    __tablename__ = "heating_forecast_day"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    # One row per future day, wholly replaced each time the forecast is computed.
    date = Column(Date, primary_key=True)
    model_id = Column(Integer, nullable=False)
    forecast_mean = Column(_DOUBLE, nullable=False)
    effective_temp = Column(_DOUBLE, nullable=False)
    expected_gas = Column(_DOUBLE, nullable=False)
    computed_at = Column(DateTime, nullable=False)


class heating_week(SQLBase):
    __tablename__ = "heating_week"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    # One row per Monday-to-Sunday week, wholly replaced on each recompute.
    week_start = Column(Date, primary_key=True)
    model_id = Column(Integer, nullable=False)
    complete_days = Column(Integer, nullable=False)
    avg_gas = Column(_DOUBLE)
    avg_degree_days = Column(_DOUBLE)
    avg_baseload = Column(_DOUBLE)
    avg_heating_gas = Column(_DOUBLE)
    efficiency = Column(_DOUBLE)
    # The WeekStatus value: 'shown' or the reason the week is left out.
    status = Column(String(20), nullable=False)

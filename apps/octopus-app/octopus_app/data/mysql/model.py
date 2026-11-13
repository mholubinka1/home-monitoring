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


class tariff_projection(SQLBase):
    __tablename__ = "tariff_projection"
    __table_args__: ClassVar[tuple[Index, dict[str, str]]] = (
        Index(
            "ix_tariff_projection_product_region_period_from",
            "product_code",
            "region",
            "period_from",
        ),
        {"schema": "octopus"},
    )

    id = Column(String(100), primary_key=True)
    product_code = Column(String(50), nullable=False)
    region = Column(String(1), nullable=False)
    period_from = Column(DateTime, nullable=False)
    period_to = Column(DateTime, nullable=False)
    unit_rate = Column(Numeric(9, 6), nullable=False)
    projected_at = Column(DateTime, nullable=False)


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
    slope = Column(_DOUBLE)
    weight = Column(_DOUBLE)
    r_squared = Column(_DOUBLE)
    baseload = Column(_DOUBLE)
    baseload_measured_days = Column(Integer)
    baseload_interpolated_days = Column(Integer)
    baseload_held_days = Column(Integer)


class thermal_model(SQLBase):
    __tablename__ = "thermal_model"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    # One row per fit, never pruned (ADR-0035).
    id = Column(Integer, primary_key=True, autoincrement=True)
    fitted_at = Column(DateTime, nullable=False)
    window_start = Column(Date, nullable=False)
    window_end = Column(Date, nullable=False)
    usable = Column(Boolean, nullable=False)
    unusable_reason = Column(String(100))
    steps_used = Column(Integer)
    heating_steps = Column(Integer)
    loss_per_degree = Column(_DOUBLE)
    heating_gain = Column(_DOUBLE)
    carried_gain = Column(_DOUBLE)
    sun_gain = Column(_DOUBLE)
    constant_gain = Column(_DOUBLE)
    backtest_days = Column(Integer)
    backtest_mean_error = Column(_DOUBLE)
    backtest_worst_error = Column(_DOUBLE)


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


class heating_hour(SQLBase):
    __tablename__ = "heating_hour"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    # One row per completed UTC hour, recomputed each run.
    hour_start = Column(DateTime, primary_key=True)
    poll_count = Column(Integer, nullable=False)
    on_minutes = Column(Integer, nullable=False)
    measured_kwh = Column(_DOUBLE)
    heating_kwh = Column(_DOUBLE)
    source = Column(String(20), nullable=False)
    unit_rate = Column(_DOUBLE)  # pence per kWh
    cost_gbp = Column(_DOUBLE)
    computed_at = Column(DateTime, nullable=False)


class comfort_day(SQLBase):
    __tablename__ = "comfort_day"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    # One row per local day; a finished day is settled once and never rewritten
    # (ADR-0034).
    date = Column(Date, primary_key=True)
    comfort_temp = Column(_DOUBLE)
    boost_starts = Column(Integer, nullable=False)
    counted_hours = Column(String(100), nullable=False)  # e.g. "0,1,6,7,12"
    minutes_below = Column(Integer)
    computed_at = Column(DateTime, nullable=False)

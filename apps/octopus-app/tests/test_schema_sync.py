from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar

import pytest
from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    Numeric,
    String,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateIndex, CreateTable

from common.config import MariaDBSettings
from octopus_app.data.mysql import model
from octopus_app.data.mysql.client import MariaDBClient
from octopus_app.data.mysql.model import SQLBase

_StrippedBase = declarative_base()


class _StrippedConsumption(_StrippedBase):
    __tablename__ = "consumption"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(String, primary_key=True)
    energy = Column(String)
    period_from = Column(DateTime, nullable=False)
    period_to = Column(DateTime, nullable=False)
    raw_value = Column(Float, nullable=False)
    est_kwh = Column(Float, nullable=False)


class _StrippedCostForecast(_StrippedBase):
    __tablename__ = "cost_forecast"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    id = Column(String, primary_key=True)
    billing_period_start = Column(Date, nullable=False)
    billing_period_end = Column(Date, nullable=False)
    actual_cost_to_date = Column(Numeric(9, 2), nullable=False)
    projected_total_cost = Column(Numeric(9, 2), nullable=False)
    computed_at = Column(DateTime, nullable=False)


class _StrippedDailyConsumptionSummary(_StrippedBase):
    __tablename__ = "daily_consumption_summary"
    __table_args__: ClassVar[dict[str, str]] = {"schema": "octopus"}

    energy = Column(String(1), primary_key=True)
    date = Column(Date, primary_key=True)
    total_kwh = Column(Numeric(8, 5), nullable=False)


def _sqlite_engine() -> Engine:
    # database="main" here and in _settings() below must agree -- see
    # ADR-0025.
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    ).execution_options(schema_translate_map={"octopus": "main"})


def _settings() -> MariaDBSettings:
    return MariaDBSettings(
        host="localhost",
        port=3306,
        database="main",
        username="test",
        password="test",
    )


def _sync_against(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> MariaDBClient:
    monkeypatch.setattr(
        "common.mariadb.client.create_engine",
        lambda *args, **kwargs: engine,
    )
    return MariaDBClient(_settings())


def test_a_table_missing_from_the_database_is_created_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine()
    tables_except_job_run = [
        table for table in SQLBase.metadata.tables.values() if table.name != "job_run"
    ]
    SQLBase.metadata.create_all(engine, tables=tables_except_job_run)

    _sync_against(engine, monkeypatch)

    columns = {column["name"] for column in inspect(engine).get_columns("job_run")}
    assert columns == {"id", "job_name", "status", "ran_at", "error_message"}


def test_a_database_with_every_table_already_present_is_left_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine()
    SQLBase.metadata.create_all(engine)

    _sync_against(engine, monkeypatch)

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    assert table_names == {table.name for table in SQLBase.metadata.tables.values()}

    for table in SQLBase.metadata.tables.values():
        columns = {column["name"] for column in inspector.get_columns(table.name)}
        assert columns == {column.name for column in table.columns}

        index_names = {index["name"] for index in inspector.get_indexes(table.name)}
        assert index_names == {index.name for index in table.indexes}


def test_a_column_missing_from_an_existing_table_is_added_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine()
    _StrippedBase.metadata.create_all(engine)

    _sync_against(engine, monkeypatch)

    columns = {column["name"] for column in inspect(engine).get_columns("consumption")}
    assert columns == {
        "id",
        "energy",
        "period_from",
        "period_to",
        "raw_value",
        "unit",
        "est_kwh",
    }


def test_a_cost_forecast_table_predating_the_energy_column_gets_it_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A pre-existing row is seeded before sync -- this is the scenario
    # ADR-0016 depends on: the live production table already has rows when
    # the energy column is added. If Schema Sync had emitted NOT NULL with
    # no server_default here (the bug Copilot caught on PR #522), MariaDB
    # would reject the ADD COLUMN outright and this row would never survive
    # to be read back below.
    engine = _sqlite_engine()
    _StrippedBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        _StrippedCostForecast(
            id="pre-existing-row",
            billing_period_start=date(2026, 7, 6),
            billing_period_end=date(2026, 8, 6),
            actual_cost_to_date=Decimal("42.50"),
            projected_total_cost=Decimal("110.00"),
            computed_at=datetime(2026, 7, 22, 4, 0, tzinfo=UTC),
        )
    )
    session.commit()

    _sync_against(engine, monkeypatch)

    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("cost_forecast")
    }
    assert "energy" in columns
    # nullable, not NOT NULL -- see ADR-0016/ADR-0005: a NOT NULL column
    # with no server_default would be rejected outright by MariaDB's
    # additive ADD COLUMN on a table that already has rows.
    assert columns["energy"]["nullable"] is True

    read_session = sessionmaker(bind=engine)()
    row = read_session.query(model.cost_forecast).filter_by(id="pre-existing-row").one()

    # Schema Sync never backfills -- the pre-existing row reads energy as
    # NULL until the manual UPDATE runs, and every other field it already
    # had survives the ADD COLUMN unchanged.
    assert row.energy is None
    assert row.actual_cost_to_date == Decimal("42.50")
    assert row.projected_total_cost == Decimal("110.00")


def test_a_daily_consumption_summary_predating_the_half_hour_count_gets_it_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine()
    _StrippedBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        _StrippedDailyConsumptionSummary(
            energy="G", date=date(2026, 1, 10), total_kwh=Decimal("24.00000")
        )
    )
    session.commit()

    _sync_against(engine, monkeypatch)

    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("daily_consumption_summary")
    }
    assert columns["half_hour_count"]["nullable"] is True

    read_session = sessionmaker(bind=engine)()
    row = read_session.query(model.daily_consumption_summary).one()
    assert row.half_hour_count is None
    assert row.total_kwh == Decimal("24.00000")


def test_a_cost_forecast_table_predating_the_estimation_flag_gets_both_columns_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A pre-existing row is seeded before sync: the NOT NULL rates_estimated
    # column is only addable to a populated table because it has a server
    # default, and that row must then read as "not estimated".
    engine = _sqlite_engine()
    _StrippedBase.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        _StrippedCostForecast(
            id="pre-existing-row",
            billing_period_start=date(2026, 7, 6),
            billing_period_end=date(2026, 8, 6),
            actual_cost_to_date=Decimal("42.50"),
            projected_total_cost=Decimal("110.00"),
            computed_at=datetime(2026, 7, 22, 4, 0, tzinfo=UTC),
        )
    )
    session.commit()

    _sync_against(engine, monkeypatch)

    columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("cost_forecast")
    }
    assert columns["rates_estimated"]["nullable"] is False
    assert columns["estimated_days"]["nullable"] is True

    read_session = sessionmaker(bind=engine)()
    row = read_session.query(model.cost_forecast).filter_by(id="pre-existing-row").one()
    assert row.rates_estimated is False
    assert row.estimated_days is None
    assert row.actual_cost_to_date == Decimal("42.50")


def test_an_index_missing_from_an_existing_table_is_created_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine()
    _StrippedBase.metadata.create_all(engine)

    _sync_against(engine, monkeypatch)

    index_names = {
        index["name"] for index in inspect(engine).get_indexes("consumption")
    }
    assert "ix_consumption_energy_period_from" in index_names


def test_every_declared_table_compiles_as_valid_mariadb_ddl() -> None:
    for table in SQLBase.metadata.tables.values():
        CreateTable(table).compile(dialect=mysql.dialect())


def test_every_declared_index_compiles_as_valid_mariadb_ddl() -> None:
    for table in SQLBase.metadata.tables.values():
        for index in table.indexes:
            # The pre-commit mypy hook's `sqlalchemy-stubs` dependency predates
            # SQLAlchemy 2.0's native types and misdeclares CreateIndex's
            # parameter as `str`; the real runtime signature (and SQLAlchemy's
            # own 2.0 stubs) correctly type it as `Index`.
            CreateIndex(index).compile(  # type: ignore[arg-type]
                dialect=mysql.dialect()
            )


def test_consumption_quantities_reject_negative_values_on_mariadb() -> None:
    table = SQLBase.metadata.tables["octopus.consumption"]
    ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))

    assert "raw_value DECIMAL(8, 5) UNSIGNED NOT NULL" in ddl
    assert "est_kwh DECIMAL(8, 5) UNSIGNED NOT NULL" in ddl


def test_consumption_values_round_trip_as_decimal_without_precision_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine()
    _sync_against(engine, monkeypatch)

    session = sessionmaker(bind=engine)()
    session.add(
        model.consumption(
            id="E20260101000000",
            energy="E",
            period_from=datetime(2026, 1, 1, tzinfo=UTC),
            period_to=datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
            raw_value=Decimal("0.12345"),
            unit="kWh",
            est_kwh=Decimal("0.12345"),
        )
    )
    session.commit()

    stored = session.query(model.consumption).one()
    assert stored.raw_value == Decimal("0.12345")
    assert stored.est_kwh == Decimal("0.12345")


def test_a_column_no_longer_declared_in_the_model_is_never_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine()
    SQLBase.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE job_run ADD COLUMN retired_field VARCHAR(10)")
        )

    _sync_against(engine, monkeypatch)

    columns = {column["name"] for column in inspect(engine).get_columns("job_run")}
    assert "retired_field" in columns

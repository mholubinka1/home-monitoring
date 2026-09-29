import logging

import pytest
from sqlalchemy import Column, DateTime, Integer, String, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from common.mariadb.client import MariaDBClientBase
from common.mariadb.model import SQLBase

# Deliberately no Index in __table_args__ and no error_message column -- used
# to seed a "table already exists but is missing a column/index" starting
# state (same pattern as the throwaway _StrippedBase/_StrippedConsumption in
# tests/test_schema_sync.py), without depending on any real app's model shape.
_StrippedBase = declarative_base()


class _StrippedJobRun(_StrippedBase):
    __tablename__ = "job_run"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)
    ran_at = Column(DateTime, nullable=False)


def _sqlite_engine() -> Engine:
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _settings() -> MariaDBSettings:
    return MariaDBSettings(
        host="localhost",
        port=3306,
        database="octopus",
        username="test",
        password="test",
    )


def _sync_against(
    engine: Engine, base: type, monkeypatch: pytest.MonkeyPatch
) -> MariaDBClientBase:
    monkeypatch.setattr(
        "common.mariadb.client.create_engine",
        lambda *args, **kwargs: engine,
    )
    return MariaDBClientBase(
        _settings(),
        declarative_base=base,
        logger=logging.getLogger("test"),
    )


def test_a_table_missing_from_the_database_is_created_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine()

    _sync_against(engine, SQLBase, monkeypatch)

    columns = {column["name"] for column in inspect(engine).get_columns("job_run")}
    assert columns == {"id", "job_name", "status", "ran_at", "error_message"}


def test_a_database_with_every_table_already_present_is_left_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine()
    SQLBase.metadata.create_all(engine)

    _sync_against(engine, SQLBase, monkeypatch)

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

    _sync_against(engine, SQLBase, monkeypatch)

    columns = {column["name"] for column in inspect(engine).get_columns("job_run")}
    assert columns == {"id", "job_name", "status", "ran_at", "error_message"}


def test_a_column_no_longer_declared_in_the_model_is_never_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine()
    SQLBase.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE job_run ADD COLUMN retired_field VARCHAR(10)")
        )

    _sync_against(engine, SQLBase, monkeypatch)

    columns = {column["name"] for column in inspect(engine).get_columns("job_run")}
    assert "retired_field" in columns


def test_an_index_missing_from_an_existing_table_is_created_on_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine()
    _StrippedBase.metadata.create_all(engine)

    _sync_against(engine, SQLBase, monkeypatch)

    index_names = {index["name"] for index in inspect(engine).get_indexes("job_run")}
    assert "ix_job_run_job_name_ran_at" in index_names

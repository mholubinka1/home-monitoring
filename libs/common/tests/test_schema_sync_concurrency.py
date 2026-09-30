import logging
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from common.mariadb.client import MariaDBClientBase
from common.mariadb.model import SQLBase


def _mysql_error(code: int, message: str) -> OperationalError:
    orig = Mock()
    orig.args = (code, message)
    return OperationalError("<statement>", {}, orig)


def _settings() -> MariaDBSettings:
    # database="main" (not "octopus"): SessionBuilder now applies its own
    # schema_translate_map keyed off settings.database (see
    # common/mariadb/client.py and ADR-0025), and "main" is SQLite's own
    # name for its default/only database, so "octopus" tables resolve there
    # unqualified.
    return MariaDBSettings(
        host="localhost",
        port=3306,
        database="main",
        username="test",
        password="test",
    )


def test_schema_sync_recovers_from_a_concurrent_table_creation_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given octopus-app and hive-app (which share octopus.job_run) start against a
    freshly-initialized database at the same moment, both can see that table
    as absent and race to create it -- the loser's CREATE TABLE fails
    "already exists". Constructing MariaDBClientBase (its public interface)
    must still succeed, recovering via a single retry, rather than crashing
    the container."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(
        "common.mariadb.client.create_engine",
        lambda *args, **kwargs: engine,
    )

    real_create_all = SQLBase.metadata.create_all
    attempts: list[int] = []

    def flaky_create_all(bind: Engine, checkfirst: bool = True) -> None:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise _mysql_error(1050, "Table 'octopus.job_run' already exists")
        real_create_all(bind, checkfirst=checkfirst)

    monkeypatch.setattr(SQLBase.metadata, "create_all", flaky_create_all)

    MariaDBClientBase(
        _settings(), declarative_base=SQLBase, logger=logging.getLogger("test")
    )

    assert attempts == [1, 2]


def test_schema_sync_does_not_swallow_an_unrelated_schema_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(
        "common.mariadb.client.create_engine",
        lambda *args, **kwargs: engine,
    )

    def failing_create_all(*args: object, **kwargs: object) -> None:
        raise _mysql_error(1046, "No database selected")

    monkeypatch.setattr(SQLBase.metadata, "create_all", failing_create_all)

    with pytest.raises(OperationalError):
        MariaDBClientBase(
            _settings(), declarative_base=SQLBase, logger=logging.getLogger("test")
        )

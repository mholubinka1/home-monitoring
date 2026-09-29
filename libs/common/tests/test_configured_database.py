import logging
import os
import uuid
from collections.abc import Iterator

import pymysql
import pytest

from common.config import MariaDBSettings
from common.mariadb.client import MariaDBClientBase
from common.mariadb.model import SQLBase

_SERVER_ENV_VARS = (
    "TEST_MARIADB_HOST",
    "TEST_MARIADB_PORT",
    "TEST_MARIADB_USER",
    "TEST_MARIADB_PASSWORD",
)
_HOST = os.environ.get("TEST_MARIADB_HOST", "")
_PORT = int(os.environ.get("TEST_MARIADB_PORT", "0"))
_USER = os.environ.get("TEST_MARIADB_USER", "")
_PASSWORD = os.environ.get("TEST_MARIADB_PASSWORD", "")


def _connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=_HOST, port=_PORT, user=_USER, password=_PASSWORD, autocommit=True
    )


def _tables_in(database: str) -> set[str]:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (database,),
        )
        return {row[0] for row in cursor.fetchall()}


@pytest.fixture
def database_name() -> Iterator[str]:
    if not all(os.environ.get(var) for var in _SERVER_ENV_VARS):
        pytest.skip(f"real-database test needs {', '.join(_SERVER_ENV_VARS)}")
    connection = _connect()
    name = f"hm_test_{uuid.uuid4().hex[:8]}"
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE `{name}`")
    yield name
    with connection.cursor() as cursor:
        cursor.execute(f"DROP DATABASE `{name}`")
    connection.close()


def test_schema_sync_and_job_runs_target_the_configured_database_not_octopus(
    database_name: str,
) -> None:
    settings = MariaDBSettings(
        host=_HOST,
        port=_PORT,
        database=database_name,
        username=_USER,
        password=_PASSWORD,
    )
    octopus_tables_before = _tables_in("octopus")

    client = MariaDBClientBase(
        settings, declarative_base=SQLBase, logger=logging.getLogger("test")
    )
    client.record_job_run("some_job", "success")

    try:
        assert client.has_successful_job_run("some_job")
    finally:
        client._session_builder.engine.dispose()
    assert "job_run" in _tables_in(database_name)
    assert _tables_in("octopus") == octopus_tables_before

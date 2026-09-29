import logging
import os
import uuid
from collections.abc import Iterator

import pymysql
import pytest

from common.config import MariaDBSettings
from common.mariadb.client import MariaDBClientBase
from common.mariadb.model import SQLBase

_HOST = os.environ.get("TEST_MARIADB_HOST", "127.0.0.1")
_PORT = int(os.environ.get("TEST_MARIADB_PORT", "3306"))
_USER = os.environ.get("TEST_MARIADB_USER", "t")
_PASSWORD = os.environ.get("TEST_MARIADB_PASSWORD", "t")


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
    try:
        connection = _connect()
    except pymysql.err.OperationalError:
        pytest.skip("no MariaDB server reachable for real-database tests")
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
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute("CREATE DATABASE IF NOT EXISTS octopus")
        octopus_tables_before = _tables_in("octopus")

    client = MariaDBClientBase(
        settings, declarative_base=SQLBase, logger=logging.getLogger("test")
    )
    client.record_job_run("some_job", "success")

    assert client.has_successful_job_run("some_job")
    assert "job_run" in _tables_in(database_name)
    assert _tables_in("octopus") == octopus_tables_before

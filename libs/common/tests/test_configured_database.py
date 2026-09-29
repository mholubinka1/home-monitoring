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


# Read lazily: parsing at import would fail collection (rather than skip) when a
# variable is defined but empty.
def _server_settings(database: str) -> MariaDBSettings:
    return MariaDBSettings(
        host=os.environ["TEST_MARIADB_HOST"],
        port=int(os.environ["TEST_MARIADB_PORT"]),
        database=database,
        username=os.environ["TEST_MARIADB_USER"],
        password=os.environ["TEST_MARIADB_PASSWORD"],
    )


def _connect() -> pymysql.connections.Connection:
    server = _server_settings("")
    return pymysql.connect(
        host=server.host,
        port=server.port,
        user=server.username,
        password=server.password,
        autocommit=True,
    )


def _tables_in(database: str) -> set[str]:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (database,),
        )
        return {row[0] for row in cursor.fetchall()}


@pytest.fixture(name="database_name")
def _create_database() -> Iterator[str]:
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
    settings = _server_settings(database_name)
    octopus_tables_before = _tables_in("octopus")

    client = MariaDBClientBase(
        settings, declarative_base=SQLBase, logger=logging.getLogger("test")
    )
    client.record_job_run("some_job", "success")

    assert client.has_successful_job_run("some_job")
    assert "job_run" in _tables_in(database_name)
    assert _tables_in("octopus") == octopus_tables_before

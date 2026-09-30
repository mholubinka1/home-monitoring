import logging

from sqlalchemy import Column, Integer, MetaData, String, Table

from common.mariadb.client import MariaDBClientBase
from common.mariadb.model import SQLBase

from .conftest import CONFIGURED_DATABASE, MariaDBContainer


def test_schema_sync_creates_tables_in_the_configured_database_not_octopus(
    configured_client: MariaDBClientBase,
    mariadb_container: MariaDBContainer,
) -> None:
    del configured_client
    assert "job_run" in mariadb_container.table_names(CONFIGURED_DATABASE)


def test_job_run_round_trips_through_the_configured_database_not_octopus(
    configured_client: MariaDBClientBase,
) -> None:
    configured_client.record_job_run("test-job", "success")

    assert configured_client.has_successful_job_run("test-job") is True


def test_raw_core_table_declared_with_octopus_schema_resolves_against_the_configured_database(
    configured_client: MariaDBClientBase,
    mariadb_container: MariaDBContainer,
) -> None:
    # Mirrors apps/octopus-app/octopus_app/data/mysql/client.py's
    # weather_observation_table: a raw Core Table on its own MetaData(),
    # never part of SQLBase's Schema Sync. Created via raw DDL against the
    # configured database directly, rather than reaching into the client's
    # internals to get at its engine.
    mariadb_container.run_sql(
        "CREATE TABLE widget (id INT PRIMARY KEY, name VARCHAR(50));",
        database=CONFIGURED_DATABASE,
    )
    mariadb_container.run_sql(
        "INSERT INTO widget (id, name) VALUES (1, 'sprocket');",
        database=CONFIGURED_DATABASE,
    )

    widget_metadata = MetaData()
    widget_table = Table(
        "widget",
        widget_metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(50)),
        schema="octopus",
    )

    with configured_client.session_read_scope() as session:
        rows = session.execute(widget_table.select()).all()

    assert [row.name for row in rows] == ["sprocket"]


def test_schema_sync_never_creates_tables_in_a_pre_existing_octopus_database(
    mariadb_container: MariaDBContainer,
) -> None:
    # octopus is created here -- unlike the other tests in this file, which
    # never create it at all -- so this test actually exercises the failure
    # mode it guards against: a real deployment's octopus database already
    # exists, and a broken schema_translate_map would make Schema Sync
    # target it instead of the configured database.
    mariadb_container.run_sql("CREATE DATABASE octopus;")
    mariadb_container.run_sql(f"CREATE DATABASE {CONFIGURED_DATABASE};")
    settings = mariadb_container.settings_for(CONFIGURED_DATABASE)

    MariaDBClientBase(
        settings, declarative_base=SQLBase, logger=logging.getLogger("test")
    )

    assert mariadb_container.table_names("octopus") == set()

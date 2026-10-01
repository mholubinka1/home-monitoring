import logging

from sqlalchemy import Column, Integer, MetaData, String, Table

from common.mariadb.client import MariaDBClientBase
from common.mariadb.model import SQLBase

from .conftest import CONFIGURED_DATABASE, MariaDBContainer


def _seed_job_run_missing_error_message_and_its_index(
    mariadb_container: MariaDBContainer, database: str
) -> None:
    mariadb_container.run_sql(
        """
        CREATE TABLE job_run (
            id INT PRIMARY KEY AUTO_INCREMENT,
            job_name VARCHAR(100) NOT NULL,
            status VARCHAR(20) NOT NULL,
            ran_at DATETIME NOT NULL
        );
        """,
        database=database,
    )


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


def test_schema_sync_adds_missing_columns_and_indexes_in_the_configured_database(
    mariadb_container: MariaDBContainer,
) -> None:
    # A pre-existing job_run missing error_message (a column) and its index
    # -- checkfirst=True skips an already-existing table, so this is the
    # only way to exercise _sync_missing_columns/_sync_missing_indexes
    # (ADD COLUMN / CREATE INDEX) rather than create_all's fresh-table path,
    # which every other test in this file goes through instead.
    mariadb_container.run_sql("CREATE DATABASE octopus;")
    mariadb_container.run_sql(f"CREATE DATABASE {CONFIGURED_DATABASE};")
    _seed_job_run_missing_error_message_and_its_index(
        mariadb_container, CONFIGURED_DATABASE
    )
    settings = mariadb_container.settings_for(CONFIGURED_DATABASE)

    MariaDBClientBase(
        settings, declarative_base=SQLBase, logger=logging.getLogger("test")
    )

    assert "error_message" in mariadb_container.column_names(
        "job_run", CONFIGURED_DATABASE
    )
    assert "ix_job_run_job_name_ran_at" in mariadb_container.index_names(
        "job_run", CONFIGURED_DATABASE
    )
    assert mariadb_container.table_names("octopus") == set()


def test_schema_sync_adds_missing_columns_when_the_configured_database_needs_quoting(
    mariadb_container: MariaDBContainer,
) -> None:
    # A hyphenated database name is unquoted-invalid SQL -- MariaDB parses
    # the hyphen as a minus operator -- so this is the exact case that would
    # break _sync_missing_columns' ALTER TABLE if it built qualified_name by
    # raw string interpolation instead of the dialect's identifier preparer.
    hyphenated_database = "home-monitoring-test"
    mariadb_container.run_sql(f"CREATE DATABASE `{hyphenated_database}`;")
    _seed_job_run_missing_error_message_and_its_index(
        mariadb_container, hyphenated_database
    )
    settings = mariadb_container.settings_for(hyphenated_database)

    MariaDBClientBase(
        settings, declarative_base=SQLBase, logger=logging.getLogger("test")
    )

    assert "error_message" in mariadb_container.column_names(
        "job_run", hyphenated_database
    )

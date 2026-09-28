"""Behaviour of scripts/rename_database.sql against a real, disposable MariaDB."""

from pathlib import Path

from .conftest import TABLES, requires_docker, run_sql

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "rename_database.sql"


def _seed_octopus(container_name: str) -> None:
    statements = ["CREATE DATABASE octopus;"]
    statements += [
        f"CREATE TABLE octopus.{table} (id INT PRIMARY KEY, v INT);" for table in TABLES
    ]
    statements.append("INSERT INTO octopus.consumption VALUES (1,1),(2,2),(3,3);")
    statements.append("INSERT INTO octopus.job_run VALUES (1,1);")
    result = run_sql(container_name, "\n".join(statements))
    assert result.returncode == 0, result.stderr


def _run_migration_script(container_name: str, **run_sql_kwargs):
    return run_sql(
        container_name, SCRIPT_PATH.read_text(encoding="utf-8"), **run_sql_kwargs
    )


def _table_names(container_name: str, database: str) -> set[str]:
    result = run_sql(
        container_name,
        f"SELECT TABLE_NAME FROM TABLES WHERE TABLE_SCHEMA='{database}';",
        database="information_schema",
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()[1:]  # drop header row
    return set(lines)


def _database_exists(container_name: str, database: str) -> bool:
    result = run_sql(
        container_name,
        f"SELECT SCHEMA_NAME FROM SCHEMATA WHERE SCHEMA_NAME='{database}';",
        database="information_schema",
    )
    assert result.returncode == 0, result.stderr
    return len(result.stdout.strip().splitlines()) > 1


def _row_count(container_name: str, database: str, table: str) -> int:
    result = run_sql(
        container_name, f"SELECT COUNT(*) FROM {table};", database=database
    )
    assert result.returncode == 0, result.stderr
    return int(result.stdout.strip().splitlines()[1])


@requires_docker
def test_migrates_all_tables_with_row_counts_preserved_and_octopus_left_empty(
    mariadb_container,
):
    _seed_octopus(mariadb_container)

    result = _run_migration_script(mariadb_container)

    assert result.returncode == 0, result.stderr
    assert _table_names(mariadb_container, "home_monitoring") == set(TABLES)
    assert _table_names(mariadb_container, "octopus") == set()
    assert _row_count(mariadb_container, "home_monitoring", "consumption") == 3
    assert _row_count(mariadb_container, "home_monitoring", "job_run") == 1


@requires_docker
def test_rejects_running_again_against_an_already_migrated_instance(mariadb_container):
    _seed_octopus(mariadb_container)
    first_run = _run_migration_script(mariadb_container)
    assert first_run.returncode == 0, first_run.stderr

    second_run = _run_migration_script(mariadb_container)

    assert second_run.returncode != 0
    assert "home_monitoring" in second_run.stderr
    assert _row_count(mariadb_container, "home_monitoring", "consumption") == 3


@requires_docker
def test_rejects_running_with_no_source_database(mariadb_container):
    result = _run_migration_script(mariadb_container)

    assert result.returncode != 0
    assert "octopus" in result.stderr
    assert not _database_exists(mariadb_container, "home_monitoring"), (
        "a failed run must not leave a dangling home_monitoring database behind -- "
        "it would falsely block a later, real retry with 'already exists'"
    )


@requires_docker
def test_recovers_after_a_failed_run_once_the_source_database_is_created(
    mariadb_container,
):
    failed_run = _run_migration_script(mariadb_container)
    assert failed_run.returncode != 0

    _seed_octopus(mariadb_container)
    retry = _run_migration_script(mariadb_container)

    assert retry.returncode == 0, retry.stderr
    assert _table_names(mariadb_container, "home_monitoring") == set(TABLES)


@requires_docker
def test_rejects_running_as_the_ordinary_app_user_and_leaves_no_dangling_database(
    mariadb_container,
):
    """Documents a real constraint (verified against actual MariaDB privilege
    behaviour, not assumed): an app user granted access to only its own database
    (the shape `MARIADB_USER` gets from `deployments/mariadb/docker-compose.yml`)
    cannot run this script -- `USE mysql`, `CREATE DATABASE`, and a cross-database
    `RENAME TABLE` all need broader privileges. RENAME_RUNBOOK.md documents running
    this as root instead. If a future change made the script work for a lesser
    user, the runbook's root-only instruction would need to change too -- this
    test exists so that change doesn't slip through unnoticed.
    """
    _seed_octopus(mariadb_container)
    run_sql(
        mariadb_container,
        "CREATE USER 'app_user'@'%' IDENTIFIED BY 'app_password'; "
        "GRANT ALL PRIVILEGES ON octopus.* TO 'app_user'@'%'; FLUSH PRIVILEGES;",
    )

    result = _run_migration_script(
        mariadb_container, user="app_user", password="app_password"
    )

    assert result.returncode != 0
    assert not _database_exists(mariadb_container, "home_monitoring"), (
        "a rejected run as an under-privileged user must not leave a dangling "
        "home_monitoring database behind either"
    )


@requires_docker
def test_rejects_running_with_a_table_missing_from_octopus(mariadb_container):
    """`octopus` existing isn't enough -- if even one of the nine tables Schema
    Sync owns is missing (dropped, mid-migration, never created), CREATE DATABASE
    would otherwise still succeed before RENAME TABLE hit the missing one,
    leaving the same dangling home_monitoring database the other guard checks
    exist to prevent.
    """
    statements = ["CREATE DATABASE octopus;"]
    statements += [
        f"CREATE TABLE octopus.{table} (id INT PRIMARY KEY, v INT);"
        for table in TABLES
        if table != "job_run"
    ]
    result = run_sql(mariadb_container, "\n".join(statements))
    assert result.returncode == 0, result.stderr

    result = _run_migration_script(mariadb_container)

    assert result.returncode != 0
    assert "job_run" in result.stderr
    assert not _database_exists(mariadb_container, "home_monitoring"), (
        "a rejected run for a missing table must not leave a dangling "
        "home_monitoring database behind either"
    )

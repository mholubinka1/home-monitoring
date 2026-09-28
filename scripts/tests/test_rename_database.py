"""Behaviour of scripts/rename_database.sql against a real, disposable MariaDB."""

from pathlib import Path

from .conftest import TABLES, query, requires_docker, run_sql

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


def _run_migration_script(container_name: str):
    return run_sql(container_name, SCRIPT_PATH.read_text(encoding="utf-8"))


def _table_names(container_name: str, database: str) -> set[str]:
    result = query(
        container_name,
        "information_schema",
        f"SELECT TABLE_NAME FROM TABLES WHERE TABLE_SCHEMA='{database}';",
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()[1:]  # drop header row
    return set(lines)


def _row_count(container_name: str, database: str, table: str) -> int:
    result = query(container_name, database, f"SELECT COUNT(*) FROM {table};")
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
    assert _table_names(mariadb_container, "home_monitoring") == set()

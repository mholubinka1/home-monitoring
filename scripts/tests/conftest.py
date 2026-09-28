"""Shared fixtures for testing operational scripts against a real, disposable MariaDB.

These tests exercise `scripts/rename_database.sql` through the same seam it actually
operates on -- a live MariaDB instance -- rather than mocking RENAME TABLE semantics.
They're skipped, not failed, when docker isn't available, since the script only ever
runs once, by hand, on the Pi; a missing docker daemon shouldn't break `pytest` for
contributors who don't have it.
"""

import shutil
import subprocess
import time
import uuid

import pytest

_ROOT_PASSWORD = "test-root-password"

TABLES = [
    "consumption",
    "agreement",
    "product",
    "product_rate",
    "daily_consumption_summary",
    "agile_forecast",
    "cost_forecast",
    "heating_status",
    "job_run",
]


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"], capture_output=True, timeout=10, check=False
    )
    return result.returncode == 0


requires_docker = pytest.mark.skipif(
    not _docker_available(), reason="docker is not available in this environment"
)


def run_sql(container_name: str, sql_text: str) -> subprocess.CompletedProcess:
    """Run `sql_text` against `container_name`'s root connection, no database selected."""
    return subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            container_name,
            "mariadb",
            "-uroot",
            f"-p{_ROOT_PASSWORD}",
        ],
        input=sql_text,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def query(
    container_name: str, database: str, sql_text: str
) -> subprocess.CompletedProcess:
    """Run a query against a specific database, returning tab-separated output with a header row."""
    return subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "mariadb",
            "-uroot",
            f"-p{_ROOT_PASSWORD}",
            database,
            "-e",
            sql_text,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def mariadb_container():
    """Start a throwaway MariaDB container, yield its name, and always tear it down."""
    name = f"rename-script-test-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "-e",
            f"MARIADB_ROOT_PASSWORD={_ROOT_PASSWORD}",
            "mariadb:latest",
        ],
        capture_output=True,
        check=True,
        timeout=30,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            result = run_sql(name, "SELECT 1;")
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"MariaDB container {name} never became ready")
        yield name
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)

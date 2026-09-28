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

# Passed on the CLI below (`-p{_ROOT_PASSWORD}`), unlike RENAME_RUNBOOK.md's real
# credentials, which go through an MYSQL_PWD env var instead to stay out of `ps`/shell
# history -- that risk doesn't apply here: this is a non-secret, hardcoded password
# confined to a throwaway, `--rm`, uniquely-named container never reachable off the
# test host.
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
    "weather_observation",
    "weather_forecast",
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


def run_sql(
    container_name: str,
    sql_text: str,
    database: str | None = None,
    user: str = "root",
    password: str = _ROOT_PASSWORD,
) -> subprocess.CompletedProcess:
    """Run `sql_text` (via stdin) against `container_name`, connecting as `user`.

    Defaults to the throwaway container's root account. With `database`, the
    connection selects that database first, same as running `mariadb <database>`
    interactively -- the returned output includes a tab-separated header row for
    any `SELECT`.
    """
    argv = [
        "docker",
        "exec",
        "-i",
        container_name,
        "mariadb",
        f"-u{user}",
        f"-p{password}",
    ]
    if database is not None:
        argv.append(database)
    return subprocess.run(
        argv,
        input=sql_text,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _wait_for_real_server(container_name: str, timeout_seconds: float = 60) -> None:
    """Wait past mariadb:latest's entrypoint, which briefly starts an internal
    setup server (to run mysql_secure_installation-equivalent steps), shuts it
    down, then starts the real one -- "ready for connections" appears in the
    container's logs twice, for the setup server and then the real one. A
    connectivity check alone can catch the setup server's brief window and
    report ready just before it closes -- the log-count check disambiguates
    which "ready" this is, then a connectivity check confirms the real one
    is actually reachable.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        logs = subprocess.run(
            ["docker", "logs", container_name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        ready_count = logs.stdout.count("ready for connections") + logs.stderr.count(
            "ready for connections"
        )
        if ready_count >= 2 and run_sql(container_name, "SELECT 1;").returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"MariaDB container {container_name} never became ready")


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
        _wait_for_real_server(name)
        yield name
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)

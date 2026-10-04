import logging
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from common.config import MariaDBSettings
from common.mariadb.client import MariaDBClientBase
from common.mariadb.model import SQLBase

# Passed on the CLI below, unlike RENAME_RUNBOOK.md's real credentials (which go
# through MYSQL_PWD instead to stay out of `ps`/shell history) -- that risk doesn't
# apply here: this is a non-secret, hardcoded password confined to a throwaway,
# `--rm`, uniquely-named container never reachable off the test host. Mirrors
# scripts/tests/conftest.py's own fixture, duplicated rather than shared since
# libs/common has no dependency on scripts/.
_ROOT_PASSWORD = "test-root-password"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"], capture_output=True, timeout=10, check=False
    )
    return result.returncode == 0


def _remove_container(name: str) -> None:
    """Force-remove the container and its volumes: the mariadb image declares an
    anonymous /var/lib/mysql volume that a bare `rm -f` (even with `--rm`) leaks."""
    subprocess.run(["docker", "rm", "-fv", name], capture_output=True, check=False)


def _run_sql(
    container_name: str,
    sql_text: str,
    database: str | None = None,
    user: str = "root",
    password: str = _ROOT_PASSWORD,
) -> subprocess.CompletedProcess:
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
        argv, input=sql_text, capture_output=True, text=True, timeout=30, check=False
    )


def _wait_for_real_server(container_name: str, timeout_seconds: float = 60) -> None:
    """mariadb:latest's entrypoint briefly starts an internal setup server before
    the real one -- "ready for connections" appears in the container's logs
    twice. A bare connectivity check alone can catch the setup server's brief
    window; the log-count check disambiguates which "ready" this is."""
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
        if ready_count >= 2 and _run_sql(container_name, "SELECT 1;").returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError(f"MariaDB container {container_name} never became ready")


def _published_port(container_name: str) -> int:
    result = subprocess.run(
        ["docker", "port", container_name, "3306/tcp"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    # e.g. "0.0.0.0:54321\n" -- take the last line in case IPv4 and IPv6 both print.
    return int(result.stdout.strip().splitlines()[-1].rsplit(":", 1)[-1])


@dataclass
class MariaDBContainer:
    name: str
    port: int

    def run_sql(self, sql_text: str, database: str | None = None) -> None:
        result = _run_sql(self.name, sql_text, database=database)
        assert result.returncode == 0, result.stderr

    def table_names(self, database: str) -> set[str]:
        result = _run_sql(self.name, "SHOW TABLES;", database=database)
        assert result.returncode == 0, result.stderr
        # Tab-separated, one header row ("Tables_in_<database>") then one table
        # name per line -- empty when the database has no tables at all.
        lines = result.stdout.strip().splitlines()
        return set(lines[1:])

    def column_names(self, table: str, database: str) -> set[str]:
        result = _run_sql(self.name, f"SHOW COLUMNS FROM {table};", database=database)
        assert result.returncode == 0, result.stderr
        # First tab-separated field of each row after the header is the
        # column name (Field | Type | Null | Key | Default | Extra).
        lines = result.stdout.strip().splitlines()
        return {line.split("\t")[0] for line in lines[1:]}

    def index_names(self, table: str, database: str) -> set[str]:
        result = _run_sql(self.name, f"SHOW INDEX FROM {table};", database=database)
        assert result.returncode == 0, result.stderr
        lines = result.stdout.strip().splitlines()
        key_name_column = lines[0].split("\t").index("Key_name")
        return {line.split("\t")[key_name_column] for line in lines[1:]}

    def settings_for(self, database: str) -> MariaDBSettings:
        return MariaDBSettings(
            host="127.0.0.1",
            port=self.port,
            database=database,
            username="root",
            password=_ROOT_PASSWORD,
        )


@pytest.fixture
def mariadb_container() -> Iterator[MariaDBContainer]:
    """Start a throwaway MariaDB container with its port published to localhost
    (needed for a real pymysql connection, unlike scripts/tests/conftest.py's
    docker-exec-only fixture), yield it, and always tear it down.

    Skips (not fails) when docker isn't available -- these tests exercise
    real MySQL/MariaDB schema-qualification semantics through the seam they
    actually operate on, not mocks, but a missing docker daemon shouldn't
    break `pytest` for contributors who don't have it.
    """
    if not _docker_available():
        pytest.skip("docker is not available in this environment")
    name = f"common-schema-test-{uuid.uuid4().hex[:8]}"
    # `docker run` is inside the try: if it times out or errors after the daemon has
    # created the container, teardown must still remove it (and its anonymous volume).
    try:
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
                "-p",
                "127.0.0.1::3306",
                "mariadb:latest",
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        _wait_for_real_server(name)
        yield MariaDBContainer(name=name, port=_published_port(name))
    finally:
        _remove_container(name)


@pytest.fixture
def remove_mariadb_container() -> Callable[[str], None]:
    """Expose the fixture's teardown helper so docker-free tests can exercise it
    (conftest functions can't be imported by test modules here)."""
    return _remove_container


# The database these container-backed tests configure MariaDBClientBase
# against -- deliberately not "octopus", so a pass can only mean
# schema_translate_map is doing its job rather than every model's literal
# schema="octopus" happening to line up with the configured database by
# coincidence.
CONFIGURED_DATABASE = "home_monitoring_test"


@pytest.fixture
def configured_client(  # pylint: disable=redefined-outer-name
    mariadb_container: MariaDBContainer,
) -> MariaDBClientBase:
    """A MariaDBClientBase built against CONFIGURED_DATABASE (not "octopus")
    on the real mariadb_container, with Schema Sync already run.

    The disable above is pytest's standard fixture-requests-fixture pattern --
    pylint has no notion of pytest's fixture injection, so a fixture
    parameter named after another fixture defined earlier in the same file
    reads as shadowing to it, not a dependency declaration.
    """
    mariadb_container.run_sql(f"CREATE DATABASE {CONFIGURED_DATABASE};")
    settings = mariadb_container.settings_for(CONFIGURED_DATABASE)
    return MariaDBClientBase(
        settings, declarative_base=SQLBase, logger=logging.getLogger("test")
    )


@pytest.fixture
def mariadb_client(monkeypatch: pytest.MonkeyPatch) -> MariaDBClientBase:
    """A MariaDBClientBase backed by an in-memory SQLite database.

    database="main" below is what SessionBuilder's own schema_translate_map
    resolves "octopus" to -- see ADR-0025.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    monkeypatch.setattr(
        "common.mariadb.client.create_engine",
        lambda *args, **kwargs: engine,
    )

    settings = MariaDBSettings(
        host="localhost",
        port=3306,
        database="main",
        username="test",
        password="test",
    )
    return MariaDBClientBase(
        settings, declarative_base=SQLBase, logger=logging.getLogger("test")
    )

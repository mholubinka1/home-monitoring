"""The docker-backed MariaDB tests must not leave anything behind on the CI runner.

The mariadb image declares an anonymous data volume. Removing its container with a
bare `docker rm -f` leaves that volume behind, and a few hundred test runs filled the
Pi's disk (CI then failed with "MariaDB container ... never became ready"). These
tests need no docker: they check the removal command the fixture would run, and drive
the real fixture against a fake `docker` to check that a start that fails, or times out,
after the container was created is still cleaned up.

This module is deliberately duplicated in scripts/tests (as are the two conftests):
libs/common has no dependency on scripts/."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest


def _assert_forced_with_volumes(argv: list[str]) -> None:
    """`argv` is a full `docker rm [options] <name>` command."""
    assert argv[:2] == ["docker", "rm"], f"expected a docker rm, got {argv}"
    options = argv[2:-1]
    short_flags = "".join(
        arg[1:] for arg in options if arg.startswith("-") and not arg.startswith("--")
    )
    assert (
        "f" in short_flags or "--force" in options
    ), f"container must be force-removed, got {argv}"
    assert (
        "v" in short_flags or "--volumes" in options
    ), f"anonymous volumes must be removed, got {argv}"


def test_the_test_container_is_removed_together_with_its_volumes(
    remove_mariadb_container: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **_: commands.append(list(argv))
    )

    remove_mariadb_container("common-schema-test-abc12345")

    assert len(commands) == 1
    assert commands[0][-1] == "common-schema-test-abc12345"
    _assert_forced_with_volumes(commands[0])


# Records every call, then fails `docker run` the way a daemon error would -- after
# the container name has been claimed.
_FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  run) exit 1 ;;
  *) exit 0 ;;
esac
"""

# Printed by the probe test only if it actually runs, i.e. if the fixture did NOT fail
# during setup.
_PROBE_RAN_MARKER = "probe test body ran"

# The probe: a test that needs the fixture. With the fake docker above, `docker run`
# exits non-zero, so the fixture must fail the probe in setup.
_PROBE_TEST = f"""
def test_probe(mariadb_container):
    raise AssertionError("{_PROBE_RAN_MARKER}")
"""

# A real `docker run` timeout would make the test wait out the fixture's timeout, so
# the timeout case has `docker run` (still logged by the fake docker) raise
# TimeoutExpired straight away.
_PROBE_TEST_WITH_RUN_TIMEOUT = """
import subprocess

import pytest

_real_run = subprocess.run


@pytest.fixture(autouse=True)
def _docker_run_times_out(monkeypatch):
    def run(argv, **kwargs):
        if argv[:2] == ["docker", "run"]:
            _real_run(argv, **{**kwargs, "check": False})
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        return _real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
""" + _PROBE_TEST

# Generous: the child normally finishes in about a second. If a regression made it wait
# (for example on `_wait_for_real_server`), the test should fail rather than hang.
_CHILD_TIMEOUT_SECONDS = 120


def _run_fixture_against_fake_docker(
    tmp_path: Path, probe: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run `probe` in a child pytest that uses this package's real conftest, with a
    fake `docker` on PATH. Returns the child's result and the docker calls it made."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(_FAKE_DOCKER)
    fake.chmod(0o755)
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(Path(__file__).parent / "conftest.py", project / "conftest.py")
    (project / "test_probe.py").write_text(probe)
    log = tmp_path / "docker.log"

    child = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"],
        cwd=project,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_DOCKER_LOG": str(log),
            "PYTHONPATH": os.pathsep.join(sys.path),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=_CHILD_TIMEOUT_SECONDS,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return child, calls


def _started_containers(calls: list[str]) -> list[str]:
    """Names passed to `docker run --name` in the fake docker's log."""
    return [
        words[words.index("--name") + 1]
        for words in (c.split() for c in calls)
        if words[:1] == ["run"]
    ]


def _removals_of(calls: list[str], name: str) -> list[list[str]]:
    """Full `docker rm [options] <name>` commands in the fake docker's log: `rm` with
    exactly `name` as the last argument (not merely a name that ends the same way)."""
    return [
        ["docker", *words]
        for words in (c.split() for c in calls)
        if words[:1] == ["rm"] and words[-1] == name
    ]


@pytest.mark.parametrize(
    "probe",
    [_PROBE_TEST, _PROBE_TEST_WITH_RUN_TIMEOUT],
    ids=["exits nonzero", "times out"],
)
def test_a_container_that_fails_to_start_is_still_removed(
    tmp_path: Path, probe: str
) -> None:
    child, calls = _run_fixture_against_fake_docker(tmp_path, probe)

    output = child.stdout + child.stderr
    assert (
        child.returncode != 0 and _PROBE_RAN_MARKER not in child.stdout
    ), f"the fixture must fail the probe test during setup, not let it run:\n{output}"
    assert calls, f"the child pytest never called docker:\n{output}"
    started = _started_containers(calls)
    assert len(started) == 1, f"expected one docker run, got {calls}"
    removals = _removals_of(calls, started[0])
    assert (
        removals
    ), f"a container whose start failed must still be removed, got {calls}"
    _assert_forced_with_volumes(removals[0])

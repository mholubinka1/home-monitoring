"""The docker-backed MariaDB tests must not leave anything behind on the CI runner.

The mariadb image declares an anonymous data volume. Removing its container with a
bare `docker rm -f` leaves that volume behind, and a few hundred test runs filled the
Pi's disk (CI then failed with "MariaDB container ... never became ready"). These
tests need no docker: they check the removal command the fixture would run."""

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest


def test_the_test_container_is_removed_together_with_its_volumes(
    remove_mariadb_container: Callable[[str], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **_: commands.append(list(argv))
    )

    remove_mariadb_container("rename-script-test-abc12345")

    assert len(commands) == 1
    argv = commands[0]
    assert argv[:2] == ["docker", "rm"]
    assert argv[-1] == "rename-script-test-abc12345"
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


# Records every call, then fails `docker run` the way a pull timeout or a daemon error
# would -- after the container name has been claimed.
_FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  run) exit 1 ;;
  *) exit 0 ;;
esac
"""

_PROBE_TEST = """
def test_probe(mariadb_container):
    raise AssertionError("the fixture should have failed during setup")
"""


def test_a_container_that_fails_to_start_is_still_removed(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(_FAKE_DOCKER)
    fake.chmod(0o755)
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(Path(__file__).parent / "conftest.py", project / "conftest.py")
    (project / "test_probe.py").write_text(_PROBE_TEST)
    log = tmp_path / "docker.log"

    subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"],
        cwd=project,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_DOCKER_LOG": str(log),
        },
        capture_output=True,
        text=True,
        check=False,
    )

    calls = log.read_text().splitlines()
    started = [
        words[words.index("--name") + 1]
        for words in (c.split() for c in calls)
        if words[0] == "run"
    ]
    assert len(started) == 1, f"expected one docker run, got {calls}"
    assert any(
        c.startswith("rm ") and c.endswith(started[0]) for c in calls
    ), f"a container whose start failed must still be removed, got {calls}"

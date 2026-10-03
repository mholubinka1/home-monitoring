"""The docker-backed MariaDB tests must not leave anything behind on the CI runner.

The mariadb image declares an anonymous data volume. Removing its container with a
bare `docker rm -f` leaves that volume behind, and a few hundred test runs filled the
Pi's disk (CI then failed with "MariaDB container ... never became ready"). These
tests need no docker: they check the removal command the fixture would run."""

import subprocess
from collections.abc import Callable

import pytest


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
    argv = commands[0]
    assert argv[:2] == ["docker", "rm"]
    assert argv[-1] == "common-schema-test-abc12345"
    flags = "".join(arg.lstrip("-") for arg in argv[2:-1] if arg.startswith("-"))
    assert "f" in flags, f"container must be force-removed, got {argv}"
    assert "v" in flags, f"anonymous volumes must be removed, got {argv}"

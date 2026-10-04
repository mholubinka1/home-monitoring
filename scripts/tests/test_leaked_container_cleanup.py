"""A cancelled CI run must not leave MariaDB test containers running on the runner.

Incident (2026-10-04): cancel-superseded-runs kills a run mid-pytest when a newer push
arrives, so the MariaDB test fixtures never reach their `finally`. `--rm` only applies when
a container exits, so the container keeps running (and keeps its anonymous volume) next to
the production database on the same Pi. Four of them were found, each created seconds
before a cancelled run ended.

The cleanup is a composite action used by the build workflow (the only workflow that runs
pytest), at the start of the job as a backstop for a run killed too hard to clean up after
itself, and again in an `if: always()` step, which still runs when a job is cancelled.
These tests run the action's real script against a fake `docker`; the workflow structure
is pinned in test_ci_workflows.py."""

import os
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "remove-leaked-test-containers" / "action.yml"
FIXTURE_CONFTESTS = [
    ROOT / "libs" / "common" / "tests" / "conftest.py",
    ROOT / "scripts" / "tests" / "conftest.py",
]

# Records every call; `ps` lists $FAKE_CONTAINERS (one name per word), and either command
# can be made to fail.
FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  ps)
    [ -n "$FAKE_PS_FAIL" ] && exit 1
    printf '%s\\n' $FAKE_CONTAINERS
    ;;
  rm)
    [ -n "$FAKE_RM_FAIL" ] && exit 1
    ;;
esac
exit 0
"""

PRODUCTION_AND_LOOKALIKES = [
    "home-monitoring-db",
    "octopus-app",
    "my-common-schema-test-0d0fb3e1",  # a prefix on the name is not ours
    "common-schema-test-0d0fb3e1-old",  # nor is a suffix
    "common-schema-test-xyz",  # nor a non-hex id
    "rename-script-test-",  # nor an empty id
    "common-schema-test-0d0fb3e",  # nor a 7-character id
    "common-schema-test-0d0fb3e1f",  # nor a 9-character id
    "common-schema-test-0D0FB3E1",  # nor uppercase hex (uuid hex is lowercase)
]


def _script() -> str:
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    (step,) = action["runs"]["steps"]
    return str(step["run"])


def _run_sweep(
    tmp_path: Path,
    containers: list[str],
    ps_fails: bool = False,
    rm_fails: bool = False,
) -> tuple["subprocess.CompletedProcess[str]", list[str]]:
    fake = tmp_path / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(0o755)
    log = tmp_path / "docker.log"
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_CONTAINERS": " ".join(containers),
    }
    if ps_fails:
        env["FAKE_PS_FAIL"] = "1"
    if rm_fails:
        env["FAKE_RM_FAIL"] = "1"
    result = subprocess.run(
        # -eo pipefail: as the Actions runner runs `shell: bash`
        ["bash", "-eo", "pipefail", "-c", _script()],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def _removals(calls: list[str]) -> list[list[str]]:
    return [c.split() for c in calls if c.split()[:1] == ["rm"]]


def test_leaked_test_containers_are_removed_with_their_volumes_and_nothing_else(
    tmp_path: Path,
) -> None:
    leaked = [
        "common-schema-test-0d0fb3e1",
        "rename-script-test-20820b0c",
        "common-schema-test-a85c2b7a",
    ]

    result, calls = _run_sweep(tmp_path, PRODUCTION_AND_LOOKALIKES + leaked)

    assert result.returncode == 0, result.stdout + result.stderr
    removals = _removals(calls)
    assert len(removals) == 1, f"expected a single docker rm, got {calls}"
    options = [arg for arg in removals[0][1:] if arg.startswith("-")]
    flags = "".join(arg[1:] for arg in options if not arg.startswith("--"))
    assert (
        "f" in flags and "v" in flags
    ), f"containers must be force-removed together with their volumes, got {options}"
    assert sorted(arg for arg in removals[0][1:] if not arg.startswith("-")) == sorted(
        leaked
    ), f"only the leaked test containers may be removed, got {removals[0]}"


def test_the_sweep_only_lists_and_removes_containers(tmp_path: Path) -> None:
    _, calls = _run_sweep(tmp_path, ["common-schema-test-0d0fb3e1"])

    assert {call.split()[0] for call in calls} == {
        "ps",
        "rm",
    }, f"the sweep may only list and remove containers (no prune, no volume or system commands): {calls}"
    listing = next(call for call in calls if call.startswith("ps"))
    assert (
        "-a" in listing.split() and "{{.Names}}" in listing
    ), f"the sweep must list every container, running or not, by name: {listing}"


def test_a_clean_runner_makes_no_removal_and_passes(tmp_path: Path) -> None:
    result, calls = _run_sweep(tmp_path, PRODUCTION_AND_LOOKALIKES)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not _removals(calls), f"nothing matched, yet docker rm ran: {calls}"
    assert "No leaked test containers" in result.stdout


def test_an_unreachable_docker_daemon_warns_and_does_not_fail_the_job(
    tmp_path: Path,
) -> None:
    result, calls = _run_sweep(tmp_path, ["common-schema-test-0d0fb3e1"], ps_fails=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" in result.stdout
    assert not _removals(calls), f"no removal may be attempted blind, got {calls}"


def test_a_failed_removal_warns_naming_what_is_left_and_does_not_fail_the_job(
    tmp_path: Path,
) -> None:
    result, _ = _run_sweep(tmp_path, ["common-schema-test-0d0fb3e1"], rm_fails=True)

    assert result.returncode == 0, result.stdout + result.stderr
    warning = next(
        line for line in result.stdout.splitlines() if line.startswith("::warning::")
    )
    assert (
        "common-schema-test-0d0fb3e1" in warning
    ), f"the warning must name the containers still to remove by hand: {warning}"


def _sweep_pattern() -> "re.Pattern[str]":
    match = re.search(r"grep -E '([^']+)'", _script())
    assert match, "the sweep script no longer greps for the container names"
    return re.compile(match.group(1))


def test_the_sweep_matches_the_names_the_test_fixtures_really_generate() -> None:
    # The two MariaDB fixtures name their container f"<prefix>-{uuid4().hex[:N]}". If a
    # fixture is renamed and the sweep is not, the sweep silently matches nothing and
    # every other test here still passes -- so tie the sweep to the fixtures' source.
    pattern = _sweep_pattern()
    for conftest in FIXTURE_CONFTESTS:
        source = conftest.read_text(encoding="utf-8")
        match = re.search(
            r'name = f"([a-z-]+-test)-\{uuid\.uuid4\(\)\.hex\[:(\d+)\]\}"', source
        )
        assert match, f"{conftest} no longer builds its container name this way"
        generated = f"{match.group(1)}-{'0123456789abcdef'[: int(match.group(2))]}"
        assert pattern.match(
            generated
        ), f"the sweep would not remove {conftest.parent.parent.name}'s container {generated!r}"

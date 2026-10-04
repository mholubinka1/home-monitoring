"""A cancelled CI run must not leave MariaDB test containers running on the runner.

Incident (2026-10-04): cancel-superseded-runs kills a run mid-pytest when a newer push
arrives, so the MariaDB test fixtures never reach their `finally`. `--rm` only applies when
a container exits, so the container keeps running (with its anonymous volume) next to the
production database on the same Pi. Four of them were found, each created seconds before a
cancelled run ended.

The cleanup is a composite action used by the build workflow (the only workflow that runs
pytest), at the start of the job as a backstop for a run killed too hard to clean up after
itself, and again in an `if: always()` step, which still runs when a job is cancelled. These
tests run the action's real script against a fake `docker`."""

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github" / "actions" / "remove-leaked-test-containers" / "action.yml"
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "ci-arm64.yml"
BUILD_JOB = "ARM64_App_Image_Build_and_Push"
ACTION_USE = "./.github/actions/remove-leaked-test-containers"

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


def test_a_failed_removal_warns_and_does_not_fail_the_job(tmp_path: Path) -> None:
    result, _ = _run_sweep(tmp_path, ["common-schema-test-0d0fb3e1"], rm_fails=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" in result.stdout


def test_the_cleanup_never_prunes_anything() -> None:
    script = _script()

    for forbidden in ("prune", "system rm", "volume rm"):
        assert forbidden not in script, f"found {forbidden!r} in the cleanup script"


def _build_steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load(BUILD_WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"][BUILD_JOB]["steps"]


def _index_of(steps: list[dict[str, Any]], name: str) -> int:
    (index,) = [i for i, step in enumerate(steps) if step.get("name") == name]
    return index


def test_the_build_job_sweeps_leaked_containers_after_checkout_and_before_the_tests() -> (
    None
):
    steps = _build_steps()
    sweeps = [i for i, step in enumerate(steps) if step.get("uses") == ACTION_USE]

    assert len(sweeps) == 2, f"expected a start and an end sweep, got steps {sweeps}"
    start = sweeps[0]
    assert start > _index_of(steps, "Checkout"), (
        "a local action only exists once the repo is checked out, so the sweep "
        "must come after the Checkout step"
    )
    assert start < _index_of(
        steps, "Run tests"
    ), "the start-of-job sweep must run before the tests that create containers"
    assert "if" not in steps[start], "the start-of-job sweep must always run"


def test_the_build_job_sweeps_again_at_the_end_even_when_cancelled() -> None:
    last = _build_steps()[-1]

    assert last.get("uses") == ACTION_USE, f"the last step must be the cleanup: {last}"
    assert (
        last.get("if") == "always()"
    ), "the final sweep must use always() so it still runs when the job is cancelled"
    assert last.get("continue-on-error") is True, (
        "the final sweep must never change the job result "
        "(for example if the job failed before Checkout and the action is missing)"
    )


def test_the_disk_guard_is_still_the_first_step_of_the_build_job() -> None:
    first = _build_steps()[0]

    assert "MIN_FREE_GB" in first.get("env", {}), f"the disk guard moved: {first}"

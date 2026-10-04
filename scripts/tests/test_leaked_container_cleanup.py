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

# Records every call. `ps` lists the containers in the $FAKE_STATE file; `rm` removes its
# arguments from that file, except any named in $FAKE_STUCK (space separated), which stay
# and make `rm` exit non-zero -- as the real `docker rm` carries on past a failure. `ps`
# can also be made to fail (daemon down).
FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  ps)
    [ -n "$FAKE_PS_FAIL" ] && exit 1
    # FAKE_PS_FAIL_AFTER=N: the (N+1)th and later listings fail.
    calls=$(cat "$FAKE_STATE.calls" 2>/dev/null || echo 0)
    echo $((calls + 1)) > "$FAKE_STATE.calls"
    [ -n "$FAKE_PS_FAIL_AFTER" ] && [ "$calls" -ge "$FAKE_PS_FAIL_AFTER" ] && exit 1
    cat "$FAKE_STATE"
    # FAKE_VANISH=name: that container exits by itself right after the first listing
    # (a `--rm` container finishing between the sweep's listing and its removal).
    if [ "$calls" -eq 0 ] && [ -n "$FAKE_VANISH" ]; then
      grep -vx "$FAKE_VANISH" "$FAKE_STATE" > "$FAKE_STATE.tmp"
      mv "$FAKE_STATE.tmp" "$FAKE_STATE"
    fi
    ;;
  rm)
    shift
    status=0
    for arg in "$@"; do
      case "$arg" in
        -*) ;;
        *)
          if ! grep -qx "$arg" "$FAKE_STATE"; then
            echo "Error: No such container: $arg" >&2
            status=1
          elif echo " $FAKE_STUCK " | grep -q " $arg "; then
            status=1
          else
            grep -vx "$arg" "$FAKE_STATE" > "$FAKE_STATE.tmp"
            mv "$FAKE_STATE.tmp" "$FAKE_STATE"
          fi
          ;;
      esac
    done
    exit $status
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
    "common-schema-test-0d0fb3eg",  # nor 8 characters that are not all hex
    "rename-script-test-zzzzzzzz",
]


def _script() -> str:
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    (step,) = action["runs"]["steps"]
    return str(step["run"])


def _run_sweep(
    tmp_path: Path,
    containers: list[str],
    ps_fails: bool = False,
    stuck: list[str] | None = None,
    ps_fails_after: int | None = None,
    vanishes: str | None = None,
) -> tuple["subprocess.CompletedProcess[str]", list[str]]:
    fake = tmp_path / "docker"
    fake.write_text(FAKE_DOCKER)
    fake.chmod(0o755)
    log = tmp_path / "docker.log"
    state = tmp_path / "containers.txt"
    state.write_text("".join(f"{name}\n" for name in containers))
    env = {
        **os.environ,
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_STATE": str(state),
        "FAKE_STUCK": " ".join(stuck or []),
    }
    if ps_fails:
        env["FAKE_PS_FAIL"] = "1"
    if ps_fails_after is not None:
        env["FAKE_PS_FAIL_AFTER"] = str(ps_fails_after)
    if vanishes:
        env["FAKE_VANISH"] = vanishes
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
    assert "No leaked test containers" not in result.stdout, (
        "a daemon that could not be asked must not be reported as a clean runner: "
        + result.stdout
    )


def test_a_failed_removal_warns_naming_what_is_left_and_does_not_fail_the_job(
    tmp_path: Path,
) -> None:
    removed, stuck = "common-schema-test-0d0fb3e1", "rename-script-test-20820b0c"

    result, _ = _run_sweep(tmp_path, [removed, stuck], stuck=[stuck])

    assert result.returncode == 0, result.stdout + result.stderr
    warning = next(
        line for line in result.stdout.splitlines() if line.startswith("::warning::")
    )
    assert (
        stuck in warning
    ), f"the warning must name the container still to remove by hand: {warning}"
    assert (
        removed not in warning
    ), f"the warning must not name a container that was removed: {warning}"


def test_a_failed_removal_whose_recheck_also_fails_names_every_container_it_tried(
    tmp_path: Path,
) -> None:
    # If the second listing fails there is no way to know what is left, so the warning
    # falls back to everything the sweep tried to remove rather than saying nothing.
    leaked = ["common-schema-test-0d0fb3e1", "rename-script-test-20820b0c"]

    result, _ = _run_sweep(tmp_path, leaked, stuck=leaked[:1], ps_fails_after=1)

    assert result.returncode == 0, result.stdout + result.stderr
    warning = next(
        line for line in result.stdout.splitlines() if line.startswith("::warning::")
    )
    assert all(
        name in warning for name in leaked
    ), f"the fallback warning must name every container the sweep tried to remove: {warning}"


def test_a_container_that_exits_by_itself_before_removal_is_not_blamed_on_the_operator(
    tmp_path: Path,
) -> None:
    # A `--rm` test container can finish between the sweep's listing and its removal;
    # `docker rm` then fails with "No such container". Nothing is left, so the sweep must
    # not tell the operator to go and remove a container that no longer exists.
    gone, stuck = "common-schema-test-0d0fb3e1", "rename-script-test-20820b0c"

    result, _ = _run_sweep(tmp_path, [gone, stuck], vanishes=gone, stuck=[stuck])

    assert result.returncode == 0, result.stdout + result.stderr
    warning = next(
        line for line in result.stdout.splitlines() if line.startswith("::warning::")
    )
    assert stuck in warning, f"the container really left must be named: {warning}"
    assert (
        gone not in warning
    ), f"a container that exited by itself is not left: {warning}"


def test_when_every_container_exited_by_itself_there_is_nothing_to_warn_about(
    tmp_path: Path,
) -> None:
    gone = "common-schema-test-0d0fb3e1"

    result, _ = _run_sweep(tmp_path, [gone], vanishes=gone)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        "::warning::" not in result.stdout
    ), f"nothing is left, so an operator has nothing to do: {result.stdout}"
    assert "nothing is left" in result.stdout


def _sweep_pattern() -> "re.Pattern[str]":
    match = re.search(r"pattern='([^']+)'", _script())
    assert match, "the sweep script no longer defines its container name pattern"
    return re.compile(match.group(1))


def test_the_sweep_matches_the_names_the_test_fixtures_really_generate() -> None:
    # The two MariaDB fixtures name their container f"<prefix>-{uuid4().hex[:N]}". If a
    # fixture is renamed and the sweep is not, the sweep silently matches nothing and
    # every other test here still passes -- so tie the sweep to the fixtures' source.
    pattern = _sweep_pattern()
    build_workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci-arm64.yml").read_text(encoding="utf-8")
    )
    guard_script = build_workflow["jobs"]["ARM64_App_Image_Build_and_Push"]["steps"][0][
        "run"
    ]
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
        assert match.group(1) in guard_script, (
            f"the disk guard's hint for finding leaked containers no longer mentions "
            f"{match.group(1)!r}, the prefix {conftest.parent.parent.name}'s fixture uses"
        )

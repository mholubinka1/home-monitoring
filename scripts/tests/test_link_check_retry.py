"""The CI link check must survive a transient connection failure without going soft on dead links.

Incident: `markdown-link-check` intermittently reported `Status: 0` (a dropped or refused
connection, not an HTTP answer) for https://docs.octopus.energy/graphql/reference/mutations/,
which returns 200 when requested directly, and failed Code Quality on `main` for the merge
of PR #567. The tool only retries on HTTP 429 and its `timeout` is per request, so neither a
longer timeout nor different headers can retry a connection that never completed. The CI
step therefore retries the whole check; a link that is genuinely dead fails every attempt
and still fails the build.

These tests run the real step script with a fake `markdown-link-check` that fails a chosen
number of times."""

import os
import subprocess
from pathlib import Path

import yaml

ACTION = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "actions"
    / "code-quality-checks"
    / "action.yml"
)

# Fails the first $FAKE_FAILURES calls, then succeeds. Counts every call.
FAKE_LINK_CHECK = """#!/bin/sh
calls_file="$FAKE_CALLS_FILE"
calls=$(cat "$calls_file" 2>/dev/null || echo 0)
calls=$((calls + 1))
echo "$calls" > "$calls_file"
if [ "$calls" -le "$FAKE_FAILURES" ]; then
  echo "  [x] https://example.test/flaky -> Status: 0"
  exit 1
fi
exit 0
"""


def _step_script() -> str:
    steps = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]
    (step,) = [s for s in steps if s.get("name") == "Check markdown links"]
    return str(step["run"])


def _run_step(tmp_path: Path, failures: int) -> tuple[subprocess.CompletedProcess[str], int]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("markdown-link-check", FAKE_LINK_CHECK),
        ("sleep", "#!/bin/sh\nexit 0\n"),  # keep the retry delay out of the test
    ):
        fake = bin_dir / name
        fake.write_text(body)
        fake.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    (work / "doc.md").write_text("# doc\n")
    calls_file = tmp_path / "calls"
    result = subprocess.run(
        # -eo pipefail: as the Actions runner runs `shell: bash`
        ["bash", "-eo", "pipefail", "-c", _step_script()],
        cwd=work,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_FAILURES": str(failures),
            "FAKE_CALLS_FILE": str(calls_file),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    calls = int(calls_file.read_text()) if calls_file.exists() else 0
    return result, calls


def test_a_transient_failure_is_retried_and_the_check_then_passes(tmp_path: Path) -> None:
    result, calls = _run_step(tmp_path, failures=1)

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == 2, f"expected one retry after the transient failure, got {calls} calls"


def test_a_link_that_stays_dead_still_fails_the_check_after_the_retries(
    tmp_path: Path,
) -> None:
    result, calls = _run_step(tmp_path, failures=99)

    assert result.returncode != 0, "a persistently failing link must still fail the build"
    assert calls == 3, f"expected three attempts in total, got {calls}"


def test_a_clean_check_runs_once(tmp_path: Path) -> None:
    result, calls = _run_step(tmp_path, failures=0)

    assert result.returncode == 0, result.stdout + result.stderr
    assert calls == 1, f"a passing check must not be repeated, got {calls} calls"

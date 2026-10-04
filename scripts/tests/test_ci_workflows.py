"""The structure the CI workflows must keep, pinned because GitHub Actions behaviour cannot
be exercised locally. Each rule exists because of a real incident on the single
self-hosted ARM64 runner (which is also the production Pi):

- superseded runs of one branch queued for 30+ minutes, while `main` builds publish the
  `latest` image that gets deployed and must never be cancelled;
- the runner's root disk filled up and CI reported "MariaDB container ... never became
  ready" instead of "disk full";
- a push during a run made the coverage-baseline bot push fail a build whose tests had
  all passed."""

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"
WORKFLOW_FILES = ["ci-arm64.yml", "ci-checks.yml"]


def _workflow(name: str) -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS_DIR / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("workflow_file", WORKFLOW_FILES)
def test_superseded_branch_runs_are_cancelled_but_main_runs_never_are(
    workflow_file: str,
) -> None:
    concurrency = _workflow(workflow_file).get("concurrency")

    assert concurrency, f"{workflow_file} declares no concurrency, so old runs pile up"
    cancel = str(concurrency["cancel-in-progress"])
    group = str(concurrency["group"])
    assert (
        "refs/heads/main" in cancel and "!=" in cancel
    ), f"cancel-in-progress must be false on main and true elsewhere, got {cancel!r}"
    assert "github.sha" in group and "refs/heads/main" in group, (
        "a main run's group must include the commit SHA, otherwise a newer pending "
        f"main run replaces an older pending one and skips its image build: {group!r}"
    )
    assert "github.workflow" in group, (
        "the group must include the workflow name, otherwise the two workflows on the "
        f"same ref cancel each other: {group!r}"
    )


SELF_HOSTED_JOBS = [
    ("ci-arm64.yml", "ARM64_App_Image_Build_and_Push"),
    ("ci-checks.yml", "checks"),
]


def _steps(workflow_file: str, job: str) -> list[dict[str, Any]]:
    return _workflow(workflow_file)["jobs"][job]["steps"]


@pytest.mark.parametrize(("workflow_file", "job"), SELF_HOSTED_JOBS)
def test_the_first_step_of_a_self_hosted_job_fails_fast_on_a_nearly_full_disk(
    workflow_file: str, job: str
) -> None:
    guard = _steps(workflow_file, job)[0]
    script = guard["run"]

    assert "MIN_FREE_GB" in guard.get(
        "env", {}
    ), "the threshold must be a named variable"
    for expected in ("exit 1", "df -h /", "docker system df"):
        assert expected in script, f"the guard must contain {expected!r}"
    for forbidden in ("rm ", "prune"):
        assert (
            forbidden not in script
        ), f"the guard must delete nothing, found {forbidden!r}"


FAKE_DF = """#!/bin/sh
case "$*" in
  *--output*) echo Avail; echo "$FAKE_AVAIL_KB" ;;
  *) echo "fake df $*" ;;
esac
"""
FAKE_DOCKER = """#!/bin/sh
echo "fake docker $*"
[ -n "$FAKE_DOCKER_FAIL" ] && exit 1
exit 0
"""


def _run_guard(
    workflow_file: str,
    job: str,
    tmp_path: Path,
    free_gb: float,
    docker_fails: bool = False,
) -> "subprocess.CompletedProcess[str]":
    guard = _steps(workflow_file, job)[0]
    for name, body in (("df", FAKE_DF), ("docker", FAKE_DOCKER)):
        fake = tmp_path / name
        fake.write_text(body)
        fake.chmod(0o755)
    env = {
        **os.environ,
        **{key: str(value) for key, value in guard["env"].items()},
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "FAKE_AVAIL_KB": str(int(free_gb * 1024 * 1024)),
    }
    if docker_fails:
        env["FAKE_DOCKER_FAIL"] = "1"
    return subprocess.run(
        # -eo pipefail: as the Actions runner runs `shell: bash`
        ["bash", "-eo", "pipefail", "-c", guard["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(("workflow_file", "job"), SELF_HOSTED_JOBS)
def test_the_disk_guard_fails_with_a_clear_message_below_the_threshold(
    workflow_file: str, job: str, tmp_path: Path
) -> None:
    result = _run_guard(workflow_file, job, tmp_path, free_gb=0.1)

    assert result.returncode == 1, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert "nearly full" in output
    assert "fake df -h /" in output
    assert "fake docker system df" in output
    assert "dangling=true" in output
    assert "builder du" in output


@pytest.mark.parametrize(("workflow_file", "job"), SELF_HOSTED_JOBS)
def test_the_disk_guard_passes_with_enough_free_space(
    workflow_file: str, job: str, tmp_path: Path
) -> None:
    result = _run_guard(workflow_file, job, tmp_path, free_gb=50)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(("workflow_file", "job"), SELF_HOSTED_JOBS)
def test_the_disk_guard_still_explains_itself_when_docker_is_down(
    workflow_file: str, job: str, tmp_path: Path
) -> None:
    # With the daemon unreachable `docker system df` fails; under `bash -e` that must
    # not cut the message short -- the operator still needs to see what to look at.
    result = _run_guard(workflow_file, job, tmp_path, free_gb=0.1, docker_fails=True)

    assert result.returncode == 1, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert "nearly full" in output
    assert "dangling=true" in output
    assert "builder du" in output


def _baseline_script() -> str:
    steps = _steps("ci-arm64.yml", "ARM64_App_Image_Build_and_Push")
    (step,) = [s for s in steps if s.get("name") == "Raise coverage baseline"]
    assert step["if"] == "success() && github.event_name == 'push'"
    return str(step["run"])


def test_the_baseline_step_fetches_the_branch_and_compares_it_with_the_tested_sha() -> (
    None
):
    script = _baseline_script()

    assert "git fetch origin" in script
    assert "GITHUB_SHA" in script
    assert "::warning::" in script, "a rejected push must be a warning, not a failure"
    assert "Raise coverage baseline to ${ACTUAL}% [skip ci]" in script


class _BaselineRepo:
    """A throwaway clone of a bare `origin` holding the baseline file, on branch `feature`."""

    BRANCH = "feature"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.origin = root / "origin.git"
        self.work = root / "work"
        self._git(root, "init", "--bare", "-b", self.BRANCH, str(self.origin))
        self.work.mkdir()
        self._git(self.work, "init", "-b", self.BRANCH)
        self._git(self.work, "remote", "add", "origin", str(self.origin))
        (self.work / ".github").mkdir()
        (self.work / ".github" / "coverage-baseline.txt").write_text("80\n")
        self._git(self.work, "add", ".")
        self._git(self.work, "commit", "-m", "tested commit")
        self._git(self.work, "push", "origin", self.BRANCH)
        fake_uv = root / "bin" / "uv"
        fake_uv.parent.mkdir()
        fake_uv.write_text('#!/bin/sh\necho "$FAKE_ACTUAL"\n')
        fake_uv.chmod(0o755)

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        identity = {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            env={**os.environ, **identity},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def tested_sha(self) -> str:
        return self._git(self.work, "rev-parse", "HEAD")

    def origin_tip(self) -> str:
        return self._git(self.origin, "rev-parse", self.BRANCH)

    def someone_else_pushes(self) -> None:
        other = self.root / "other"
        self._git(self.root, "clone", "-b", self.BRANCH, str(self.origin), str(other))
        (other / "new.txt").write_text("x")
        self._git(other, "add", ".")
        self._git(other, "commit", "-m", "pushed during the run")
        self._git(other, "push", "origin", self.BRANCH)

    def reject_commits(self) -> None:
        hook = self.work / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)

    def remove_baseline_file(self) -> None:
        (self.work / ".github" / "coverage-baseline.txt").unlink()

    def reject_all_pushes(self) -> None:
        hook = self.origin / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)

    def make_origin_unreachable(self) -> None:
        self._git(
            self.work,
            "remote",
            "set-url",
            "origin",
            str(self.root / "no-such-remote.git"),
        )

    def run_step(
        self, actual: int, tested_sha: str
    ) -> "subprocess.CompletedProcess[str]":
        env = {
            **os.environ,
            "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}",
            "FAKE_ACTUAL": str(actual),
            "GITHUB_REF_NAME": self.BRANCH,
            "GITHUB_SHA": tested_sha,
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
        return subprocess.run(
            [
                "bash",
                "-e",
                "-c",
                _baseline_script(),
            ],  # -e: as the Actions runner runs `shell: bash`
            cwd=self.work,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


def test_a_risen_coverage_on_an_unmoved_branch_pushes_the_baseline_commit(
    tmp_path: Path,
) -> None:
    repo = _BaselineRepo(tmp_path)
    tested = repo.tested_sha()

    result = repo.run_step(actual=85, tested_sha=tested)

    assert result.returncode == 0, result.stdout + result.stderr
    assert repo.origin_tip() != tested, "the baseline commit was not pushed"
    subject = repo._git(repo.origin, "log", "-1", "--format=%s", repo.BRANCH)
    assert subject == "Raise coverage baseline to 85% [skip ci]"


def test_a_risen_coverage_on_a_moved_branch_skips_the_raise_without_failing(
    tmp_path: Path,
) -> None:
    repo = _BaselineRepo(tmp_path)
    tested = repo.tested_sha()
    repo.someone_else_pushes()
    moved_tip = repo.origin_tip()

    result = repo.run_step(actual=85, tested_sha=tested)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "branch moved" in result.stdout.lower()
    assert repo.origin_tip() == moved_tip, "must not push when the branch has moved"
    assert repo.tested_sha() == tested, "must not leave a stray local commit"


def test_a_commit_that_fails_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    # Not only the push: every command on the raise path runs under the runner's
    # fail-fast bash, so each one needs its own guard.
    repo = _BaselineRepo(tmp_path)
    tested = repo.tested_sha()
    repo.reject_commits()

    result = repo.run_step(actual=85, tested_sha=tested)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" in result.stdout
    assert repo.origin_tip() == tested, "nothing may be pushed when the commit failed"


def test_an_unreadable_baseline_file_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    repo = _BaselineRepo(tmp_path)
    tested = repo.tested_sha()
    repo.remove_baseline_file()

    result = repo.run_step(actual=85, tested_sha=tested)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" in result.stdout


def test_the_baseline_step_is_also_marked_continue_on_error_as_a_backstop() -> None:
    steps = _steps("ci-arm64.yml", "ARM64_App_Image_Build_and_Push")
    (step,) = [s for s in steps if s.get("name") == "Raise coverage baseline"]

    assert step.get("continue-on-error") is True, (
        "raising the baseline is a nicety, never a gate: anything the explicit "
        "guards miss must still not fail the job"
    )


def test_a_push_that_is_still_rejected_is_a_warning_not_a_failure(
    tmp_path: Path,
) -> None:
    repo = _BaselineRepo(tmp_path)
    tested = repo.tested_sha()
    repo.reject_all_pushes()

    result = repo.run_step(actual=85, tested_sha=tested)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::" in result.stdout


def test_an_unreachable_origin_skips_the_raise_without_failing(tmp_path: Path) -> None:
    # A failed fetch is a different trigger from a moved branch (same fallback, so it
    # needs its own test): the step cannot tell whether the branch moved, so it must
    # skip rather than risk pushing, and still never fail the build.
    repo = _BaselineRepo(tmp_path)
    tested = repo.tested_sha()
    repo.make_origin_unreachable()

    result = repo.run_step(actual=85, tested_sha=tested)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipping the baseline raise" in result.stdout.lower()
    assert repo.tested_sha() == tested, "must not leave a stray local commit"


def test_coverage_that_has_not_risen_makes_no_commit(tmp_path: Path) -> None:
    repo = _BaselineRepo(tmp_path)
    tested = repo.tested_sha()

    result = repo.run_step(actual=80, tested_sha=tested)

    assert result.returncode == 0, result.stdout + result.stderr
    assert repo.tested_sha() == tested
    assert repo.origin_tip() == tested

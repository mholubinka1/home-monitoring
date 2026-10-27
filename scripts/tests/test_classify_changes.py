"""What the CI change classifier says about a push, which decides how much of CI runs.

The classifier runs against a throwaway repo with an `origin/main`, as it does in CI after
a full-history checkout."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[2] / ".github" / "scripts" / "classify-changes.sh"
)


class _Repo:
    """A repo whose `main` (also published as `origin/main`) holds one file of each kind."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._git("init", "-b", "main")
        for path in (
            "README.md",
            "apps/octopus-app/octopus_app/main.py",
            "apps/octopus-app/tests/test_main.py",
            "data/grafana/gas.json",
        ):
            self.write(path)
        self.commit("base")
        self._git("update-ref", "refs/remotes/origin/main", "HEAD")

    def _git(self, *args: str) -> str:
        identity = {
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        }
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            env={**os.environ, **identity},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def write(self, path: str, text: str = "x\n") -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, message: str) -> None:
        self._git("add", "-A")
        self._git("commit", "-m", message)

    def branch(self, name: str) -> None:
        self._git("checkout", "-b", name)

    def change(self, *paths: str) -> None:
        for path in paths:
            self.write(path, f"changed {path}\n")
        self.commit("change")

    def classify(self, ref_name: str) -> dict[str, str]:
        output = self.root.parent / "github_output"
        output.write_text("")
        subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=self.root,
            env={
                **os.environ,
                "GITHUB_REF_NAME": ref_name,
                "GITHUB_OUTPUT": str(output),
            },
            capture_output=True,
            text=True,
            check=True,
        )
        return dict(
            line.split("=", 1) for line in output.read_text().splitlines() if line
        )


@pytest.fixture
def repo(tmp_path: Path) -> _Repo:
    root = tmp_path / "repo"
    root.mkdir()
    return _Repo(root)


def test_a_branch_changing_only_docs_runs_the_docs_lane_without_images(
    repo: _Repo,
) -> None:
    repo.branch("feature")
    repo.change("README.md", ".agent-docs/specs/thing.md", "docs/guide.txt")

    result = repo.classify("feature")

    assert result["lane"] == "docs"
    assert result["images"] == "false"


def test_a_branch_changing_grafana_dashboards_and_docs_runs_the_grafana_lane(
    repo: _Repo,
) -> None:
    repo.branch("feature")
    repo.change("data/grafana/gas.json", "README.md")

    result = repo.classify("feature")

    assert result["lane"] == "grafana"
    assert result["images"] == "false"


def test_a_branch_changing_only_tests_runs_the_code_lane_without_images(
    repo: _Repo,
) -> None:
    repo.branch("feature")
    repo.change("apps/octopus-app/tests/test_main.py")

    result = repo.classify("feature")

    assert result["lane"] == "code"
    assert result["images"] == "false"


@pytest.mark.parametrize(
    "path",
    [
        "apps/octopus-app/octopus_app/main.py",
        "apps/hive-app/hive_app/main.py",
        "libs/common/common/config.py",
        "deployments/octopus-app/Dockerfile",
        "uv.lock",
        "pyproject.toml",
        "apps/hive-app/pyproject.toml",
    ],
)
def test_a_branch_changing_an_image_input_runs_the_code_lane_with_images(
    repo: _Repo, path: str
) -> None:
    repo.branch("feature")
    repo.change(path)

    result = repo.classify("feature")

    assert result["lane"] == "code"
    assert result["images"] == "true"


def test_a_branch_changing_a_workflow_runs_the_code_lane(repo: _Repo) -> None:
    repo.branch("feature")
    repo.change(".github/workflows/ci.yml")

    assert repo.classify("feature")["lane"] == "code"


def test_code_anywhere_in_a_branch_wins_over_grafana_and_docs(repo: _Repo) -> None:
    repo.branch("feature")
    repo.change("apps/octopus-app/tests/test_main.py")
    repo.change("data/grafana/gas.json", "README.md")

    assert repo.classify("feature")["lane"] == "code"


def test_a_branch_with_no_merge_base_falls_back_to_the_full_run(repo: _Repo) -> None:
    repo._git("checkout", "--orphan", "unrelated")
    repo.change("README.md")

    result = repo.classify("unrelated")

    assert result["lane"] == "code"
    assert result["images"] == "true"


def test_without_origin_main_the_classifier_falls_back_to_the_full_run(
    repo: _Repo,
) -> None:
    repo.branch("feature")
    repo.change("README.md")
    repo._git("update-ref", "-d", "refs/remotes/origin/main")

    result = repo.classify("feature")

    assert result["lane"] == "code"
    assert result["images"] == "true"


def test_a_branch_with_no_changes_yet_runs_the_docs_lane(repo: _Repo) -> None:
    repo.branch("feature")

    result = repo.classify("feature")

    assert result["lane"] == "docs"
    assert result["images"] == "false"
    assert result["changed_md"] == ""


def test_a_docs_merge_on_main_is_judged_against_main_before_it(repo: _Repo) -> None:
    repo.change("apps/octopus-app/octopus_app/main.py")  # an earlier code merge
    repo.branch("feature")
    repo.change("README.md")
    repo._git("checkout", "main")
    repo._git("merge", "--no-ff", "-m", "merge docs", "feature")

    result = repo.classify("main")

    assert result["lane"] == "docs"
    assert result["images"] == "false"


def test_a_code_merge_on_main_runs_the_code_lane_with_images(repo: _Repo) -> None:
    repo.branch("feature")
    repo.change("apps/octopus-app/octopus_app/main.py")
    repo.change("README.md")
    repo._git("checkout", "main")
    repo._git("merge", "--no-ff", "-m", "merge code", "feature")

    result = repo.classify("main")

    assert result["lane"] == "code"
    assert result["images"] == "true"


def test_changed_markdown_lists_only_the_markdown_files_that_still_exist(
    repo: _Repo,
) -> None:
    repo.write("docs/old.md")
    repo.commit("add a doc")
    repo._git("update-ref", "refs/remotes/origin/main", "HEAD")
    repo.branch("feature")
    repo.change("README.md", ".agent-docs/specs/new.md", "docs/guide.txt")
    (repo.root / "docs" / "old.md").unlink()
    repo.commit("remove a doc")

    result = repo.classify("feature")

    assert sorted(result["changed_md"].split()) == [
        ".agent-docs/specs/new.md",
        "README.md",
    ]

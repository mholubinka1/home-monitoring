# Speed up CI on the single runner

## Problem Statement

All CI runs on one self-hosted ARM64 runner (4 CPUs, 3.7 GB RAM), which is also the
production Pi, and it runs one job at a time. Every push to any branch runs two workflows
regardless of what changed:

- Code Quality Checks: about 5 minutes of work, but a median of 39 minutes queued
  behind the other workflow.
- Docker Build/Publish Image - ARM64: about 29 minutes, of which the pytest run is about
  27 minutes. It then builds both images and pushes them to Docker Hub, as `dev` from
  branches and `latest` from `main`.

So a push costs about 34 minutes of runner time even when it only changes Markdown.
Over three weeks, 22 of 32 merges to `main` touched only docs or Grafana dashboards. Each
Dependabot PR also costs a full run, and Dependabot checks daily. Nothing on `main` is
protected by a required check, so a PR can merge with red CI.

Most of the test time goes to a few things:

- The heating job tests: about 14 minutes on CI, because each test runs a full brute-force
  heating model fit.
- Coverage tracing, which makes CPU-bound tests about 4× slower.
- Two pricing tests that wait 20 seconds each on a real retry backoff.

## Solution

One workflow with two jobs, `checks` and then `build`, that only does the work a change
needs:

- A **docs-only change** (Markdown, `.agent-docs/`, `docs/`) runs the cheap lints only and
  is green in about 5 minutes. The `build` job is skipped.
- A **Grafana-only change** (`data/grafana/`) also runs the quick scripts tests that read
  the dashboards.
- A **code change** runs every check and the full test suite, which is much cheaper now,
  and is green in about 15 minutes.
- **Images** are built only when something that goes into them changed. They are pushed
  only from `main`. Branches build them to prove the Dockerfiles still work.
- Dependabot opens routine update PRs weekly. Security updates are unaffected.
- After merge, both jobs become required checks on `main`.

## User Stories

1. As the repo owner, I want a docs-only push to be green in about 5 minutes, so that
   editing Markdown or agent docs doesn't tie up the runner for half an hour.
2. As the repo owner, I want a Grafana-dashboard-only push to run only the tests that
   read dashboards, so that dashboard tweaks are quick and still checked.
3. As the repo owner, I want a code push to be green in about 15 minutes with no
   separate queue wait between the checks and the tests, so that I get feedback quickly.
4. As the repo owner, I want a failing lint to stop the run before the test suite starts,
   so that a broken push doesn't spend 10 minutes on tests first.
5. As the repo owner, I want `main` to keep running tests on code merges, so that two
   PRs that pass separately but break together are still caught.
6. As the repo owner, I want docs-only merges to `main` to skip tests and image builds,
   so that most merges finish quickly.
7. As the repo owner, I want `latest` images published only from `main`, and only when
   an image input changed, so that Docker Hub and the runner do no pointless work.
8. As the repo owner, I want branch pushes that change an image input to build both
   images without pushing them, so that a broken Dockerfile shows up before merge.
9. As the repo owner, I want CI to be a required check on `main`, so that nothing merges
   red.
10. As the repo owner, I want CI to fall back to the full run whenever it can't tell what
    changed, so that a classification bug can never skip tests that should have run.
11. As the repo owner, I want the markdown link check to check only the Markdown files a
    change touched, so that code pushes don't spend a minute fetching every external link.
12. As the repo owner, I want Dependabot's routine update PRs weekly instead of daily, so
    that dependency churn doesn't dominate the runner.
13. As a developer, I want the heating job tests to still prove the job's behaviour
    (store, verdicts, forecast, weeks, refit timing) while running much faster, so that
    the suite's coverage of the job is unchanged.

## Implementation Decisions

- **One workflow replaces two.** `ci-checks.yml` and `ci-arm64.yml` are replaced by one
  workflow with two jobs:
  - `checks`, the quality checks.
  - `build`, which `needs: checks` and runs the tests, coverage baseline raise and images.

  It keeps the existing trigger (push to any branch, never `pull_request`, per ADR-0012)
  and the existing concurrency rule (cancel superseded branch runs; `main` runs are keyed
  by SHA and never cancelled). Both jobs keep the disk guard as their first step. `build`
  keeps the start and end leaked-container sweeps.
- **The quality-checks composite action is inlined** into the `checks` job. It existed only
  to share steps with the fork-PR workflow, which ADR-0013 removed.
- **A change classifier script** runs early in `checks` and exposes its results as job
  outputs:
  - On a branch, it diffs the branch tip against its merge-base with `origin/main`. This
    means the whole branch, not just the last push.
  - On `main`, it diffs from where `main` was before the push, so a push of several
    commits is judged as a whole. If that commit is unknown, it uses the first parent.
  - Renames count at both their old and new path.

  It reports a `lane`, plus `images` and `changed_md`:
  - `lane=docs`: every changed path is `*.md`, `.agent-docs/**` or `docs/**`.
  - `lane=grafana`: some changed path is under `data/grafana/` and nothing else is code.
  - `lane=code`: any other path, including `.github/`, `pyproject.toml`, `uv.lock` and
    `deployments/`.
  - `images`: an image input changed. Image inputs are `deployments/**`, app and lib
    source outside tests, any `pyproject.toml` and `uv.lock`.
  - `changed_md`: the list of changed Markdown files.

  If the diff can't be computed (no merge-base, no parent, git error), the classifier
  reports `code` and `images`, which is the full run. An empty diff is treated as docs.
- **`checks` lanes.** Always run:
  - the disk guard and checkout,
  - markdownlint,
  - codespell,
  - yamllint,
  - actionlint,
  - shellcheck,
  - gitleaks (kept, as today).

  The markdown link check runs only on `changed_md`, and is skipped when that list is
  empty. When `code` is set, the job also runs `uv lock --check`, `uv sync`, pyproject
  validation, `uv audit`, mypy, isort, black, ruff, pylint (unchanged) and bandit.
- **`build` lanes.** The job is skipped at job level when the change is docs-only. A
  skipped job satisfies a required check.
  - Grafana-only: sync and run only the scripts tests.
  - Code: the full suite with coverage under `COVERAGE_CORE=sysmon`, then the coverage
    baseline raise as today.
  - `images`: build both images. Log in and push only on `main`, as `latest`. On branches,
    build without pushing, and the `dev` tag goes away.
- **Dependabot** `uv` updates move from `daily` to `weekly`.
- **Heating job tests.** An autouse fixture in the heating job tests replaces
  `heating_model.fit_heating_model` with a `functools.partial` of the original that uses a
  coarse grid: thresholds 8.0 to 18.0 °C in 0.5 °C steps, weights 0 to 0.8 in 0.2 steps.
  The grid constants are bound as default arguments, so patching the constants would have
  no effect. One test opts out with a `full_fit_grid` marker, because the bootstrap's
  upper threshold sits only a few 0.1 °C steps above the fitted one. Seeded histories
  shrink from 730 days to 485, the fit window plus the baseload window: all the history a
  fit can use. `heating_model.py` itself does not change.
- **Pricing tests.** The two tests that fail a rate fetch patch
  `octopus_app.common.decorator.time.sleep`, as the transport tests already do.
- **Required checks** (after merge, via the GitHub API): both jobs become required status
  checks on `main`. Branches are not required to be up to date.

## Testing Decisions

- **Classifier.** Run as a subprocess against a throwaway git repo, the same pattern as
  `_BaselineRepo` in `scripts/tests/test_ci_workflows.py`. Assert its outputs for:
  - a docs-only branch,
  - a Grafana-only branch,
  - a code branch,
  - a tests-only code branch (code but no images),
  - an image-input branch,
  - a mixed branch,
  - a branch with no merge-base (falls back to the full run),
  - an empty diff,
  - a commit on `main` judged against its first parent.
- **Workflow structure.** `scripts/tests/test_ci_workflows.py` is retargeted at the new
  workflow. Its existing rules carry over to the right jobs: concurrency, disk guard first,
  baseline step, sweeps, and pytest only in a job that sweeps. New rules pin the lanes:
  - `build` needs `checks` and is skipped for docs-only.
  - The Python checks are gated on `code`.
  - The link check is gated on `changed_md`.
  - Images push only on `main`.
- **Test-suite speedups.** These change no behaviour, so they get no new tests. The
  existing heating job and pricing tests must still pass. Their speed is checked with
  `--durations` locally, then confirmed by real CI timings after merge.

## Out of Scope

- Any change to `heating_model.py` or other production code.
- Adding runners.
- Parallel tests (pytest-xdist). This is reconsidered only if code pushes stay over 15
  minutes after this lands.
- Dropping or parallelising pylint, and dropping gitleaks. The owner chose to keep both
  as they are.
- Requiring branches to be up to date before merge.

## Further Notes

Baseline, from `gh` run data for 9–27 October 2026:

- Quality checks run for 285 s. The biggest steps are pylint (111 s), the link check
  (72 s) and gitleaks (43 s).
- pytest takes 1593 s on CI against 83 s locally, where 17 Docker tests are skipped.
- `test_heating_job.py` takes 835 s on CI.
- Locally, `test_heating_job.py` takes 28 s with no coverage, 107 s with `--cov` and 83 s
  under sysmon.

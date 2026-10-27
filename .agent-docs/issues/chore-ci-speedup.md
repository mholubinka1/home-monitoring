# Issues: chore/ci-speedup

> Work complete — PR ready to merge.

## Cheaper heating job and pricing tests

**Blocked by**: None

**User stories**: 13

### What to build

The heating job tests run each fit on a coarse grid. An autouse fixture replaces the
model's fit function with a partial of the original using thresholds in 0.5 °C steps and
weights in 0.2 steps. Seeded histories are shortened to what each test needs. The two
pricing tests that fail a rate fetch patch the retry sleep, as the transport tests
already do. Production code is unchanged.

### Acceptance criteria

- [x] Given the heating job tests, when they run, then every existing assertion still
      holds: store, verdicts, forecast, weeks, refit timing, and closeness to the planted
      model.
- [x] Given the heating job tests with coverage, when timed locally, then they take a
      small fraction of today's 107 s.
- [x] Given the two pricing tests that fail a rate fetch, when they run, then neither
      waits on a real sleep.
- [x] `heating_model.py` and other production code are unchanged.

---

## Change classifier

**Blocked by**: None

**User stories**: 1, 2, 6, 10

### What to build

A small script that says what kind of change a push is. On a branch, it compares the
tip with its merge-base with `main`. On `main`, it compares from where `main` was
before the push. It prints GitHub Actions outputs:

- `docs`
- `grafana`
- `code`
- `images`
- `changed_md` (the list of changed Markdown files)

If it can't compute the diff, it reports the full run (`code` and `images`). It is tested
by subprocess against a throwaway git repo.

### Acceptance criteria

- [x] Given only `*.md`, `.agent-docs/` or `docs/` changes, then the result is docs, with
      no code and no images.
- [x] Given `data/grafana/` changes (with or without docs), then the result is grafana,
      with no code.
- [x] Given a test-only change, then the result is code, with no images.
- [x] Given app or lib source, `deployments/`, `pyproject.toml` or `uv.lock` changes, then
      the result is code and images.
- [x] Given `.github/` changes, then the result is code.
- [x] Given no merge-base or a git error, then the result is code and images.
- [x] Given an empty diff, then the result is docs.
- [x] Given a push to `main`, then all its commits are judged together, from where
      `main` was before the push, or from the first parent if that is unknown.
- [x] `changed_md` lists exactly the changed Markdown files that still exist.

---

## One workflow, two jobs

**Blocked by**: None

**User stories**: 3, 4, 5

### What to build

Replace the two workflows with one push-only workflow. It has a `checks` job and a
`build` job that needs it. The composite quality action is inlined into `checks`, and the
old workflow files and action are removed. Disk guards, concurrency, sweeps and the
coverage baseline raise carry over. pytest runs with `COVERAGE_CORE=sysmon`.
test_ci_workflows.py is retargeted at the new file. In this slice, every push still does
all the work.

### Acceptance criteria

- [x] Given any push, when CI runs, then `checks` runs first and `build` starts only if
      `checks` passed.
- [x] Given a lint failure, then `build` never starts.
- [x] The concurrency rules hold: superseded branch runs are cancelled, and `main` runs
      are keyed by SHA and never cancelled.
- [x] Both jobs start with the disk guard, and `build` keeps both leaked-container sweeps.
- [x] pytest runs with `COVERAGE_CORE=sysmon`.
- [x] The old workflows and the composite action are gone, and test_ci_workflows.py passes
      against `ci.yml`.

---

## Lanes: docs, Grafana and code

**Blocked by**: #698, #699

**User stories**: 1, 2, 3, 5, 6, 10, 11

### What to build

Wire the classifier into `checks` as job outputs:

- Python checks run only for code.
- The link check runs only on changed Markdown files.
- `build` is skipped for docs-only changes.
- `build` runs only the tests with `grafana` in their name for Grafana-only changes.
- `build` runs the full suite for code.

### Acceptance criteria

- [x] Given a docs-only push, then only the lints run, `build` is skipped, and the run is
      green.
- [x] Given a Grafana-only push, then `build` runs only the tests that read the
      dashboards.
- [x] Given a code push, then every Python check and the full suite run.
- [x] Given no changed Markdown, then the link check is skipped. Otherwise it checks only
      the changed files.
- [x] Given the classifier fell back, then the full run happens.

---

## Images only when needed

**Blocked by**: #700

**User stories**: 7, 8

### What to build

Build both images only when an image input changed. Log in and push only on `main`, as
`latest`. On branches, build without pushing. The `dev` tag is no longer produced.

### Acceptance criteria

- [x] Given a branch push that changes image inputs, then both images build and nothing
      is pushed.
- [x] Given a code merge to `main` that changes image inputs, then both images are pushed
      as `latest`.
- [x] Given a change with no image inputs (tests only, docs, Grafana), then no image is
      built.

---

## Dependabot weekly

**Blocked by**: None

**User stories**: 12

### What to build

Move Dependabot's `uv` version updates from daily to weekly. Security updates stay
enabled and unaffected.

### Acceptance criteria

- [x] The Dependabot `uv` schedule is weekly, and the grouping is unchanged.

---

## Required checks on main

**Blocked by**: #701, plus this PR merged and the new jobs having run on `main`

**User stories**: 9

### What to build

After merge, make both new jobs required status checks on `main` through the GitHub API.
Branches are not required to be up to date.

### Acceptance criteria

- [x] Given a PR whose `checks` or `build` job failed, then it cannot merge.
- [x] Given a docs-only PR whose `build` job was skipped, then it can merge.
- [x] Branches are not required to be up to date before merging.

---

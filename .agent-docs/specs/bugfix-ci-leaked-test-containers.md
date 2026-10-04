# Remove MariaDB test containers leaked by a cancelled CI run

## Problem Statement

CI cancels a superseded run mid-pytest (`concurrency`, #577). The MariaDB test
fixtures then never reach their `finally`, and because `--rm` only applies once
a container exits, the container keeps running, with its anonymous volume, on
the self-hosted runner, which is also the production Pi. On 2026-10-04 four
such containers were found next to the production database; each had been
created seconds before a cancelled run ended.

## Solution

A composite action sweeps containers the fixtures name, and the build workflow
runs it twice: once at the start of the job as a backstop, and once as the last
step with `if: always()`, which still runs when a job is cancelled.

## User Stories

1. As the operator of the Pi, I want a cancelled CI run to clean up its own test
   containers, so that idle database servers do not pile up next to production.
2. As the operator, I want the cleanup to touch only the fixtures' containers,
   so that production containers can never be removed by it.
3. As a developer, I want housekeeping never to fail or hang a build, so that a
   docker hiccup does not turn a green build red or hold the single runner.

## Implementation Decisions

- The action lists every container by name and removes those matching exactly
  `^(common-schema-test|rename-script-test)-[0-9a-f]{8}$` with `docker rm -fv`
  (the names the two fixtures generate). It never prunes and removes nothing
  else.
- It warns, and exits 0, if docker cannot be listed or a removal fails; the
  warning names only what is really still there (a `--rm` container can exit on
  its own between listing and removal).
- Used in `ci-arm64.yml` only, because only that workflow runs pytest. Both uses
  are `continue-on-error: true` with `timeout-minutes`. The start sweep sits
  after Checkout (a local action needs the repo) and before the tests; the final
  sweep is the last step with `if: always()`.
- The disk guard stays the first step. If leaked volumes fill the disk the guard
  fails before any sweep can run, so the guard's hint names how to find leaked
  containers; the sweep is a backstop only while the disk is not already full.
- Assumes one job at a time on the single runner and nobody running the MariaDB
  tests by hand on the host while a job starts or ends; documented in the action
  and the workflow.

## Testing Decisions

- The action's real script runs against a fake `docker` under
  `bash -eo pipefail` (as Actions runs `shell: bash`): exact removal, lookalike
  and production names untouched, clean runner, daemon down, stuck container,
  recheck failure, container exiting by itself.
- A test ties the sweep's pattern, and the guard's hint, to the container names
  read from the two conftests, so renaming a fixture fails a test.
- Structural tests in `scripts/tests/test_ci_workflows.py` pin the placement,
  `always()`, `continue-on-error`, `timeout-minutes`, the disk guard staying
  first, and that `ci-checks.yml` runs no pytest.
- Mutation-checked; and proven live on the PR by cancelling a run mid-test.

## Out of Scope

- Pruning images, build cache or unused volumes (the disk guard deliberately
  deletes nothing).
- Making the fixtures survive a killed process (not possible from inside the
  process).
- A second runner; the sweep assumes there is one.

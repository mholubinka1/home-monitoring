# CI hardening: tolerant coverage baseline, cancel superseded runs, low-disk guard, steadier link check

## Problem Statement

Four CI problems showed up this week on the single self-hosted ARM64 runner, which is also the production Pi.

1. **A baseline push can fail a green build.** The "Raise coverage baseline" step pushes a bot commit to the triggering branch. If anyone pushes to that branch while the job runs, the push is rejected and the whole job fails even though every test passed (PR #563).
2. **Superseded runs pile up.** Every push to every branch starts two workflows on one runner. A burst of pushes queued runs for 30+ minutes while older, now-irrelevant runs of the same branch still executed.
3. **A full disk looked like a flaky test.** The runner's root disk filled to 100% (84 MB free) because the test fixtures leaked docker volumes (fixed in #573). CI then failed with "MariaDB container ... never became ready", which pointed nowhere near the cause.
4. **The link checker flakes.** `markdown-link-check` intermittently reports `Status: 0` for a link that returns 200 when requested directly, and it failed Code Quality on `main` for the #567 merge.

## Solution

- The baseline step never fails the job. It skips the raise when the remote branch has moved on, and treats a push that still cannot land as a warning.
- A new push to a branch cancels that branch's older runs. Runs on `main` are never cancelled, because `main` builds publish the `latest` image that gets deployed.
- A first step in every self-hosted job fails fast with a clear message when the runner's root disk is nearly full. It deletes nothing.
- The CI link check retries the whole check on failure, so a transient connection failure passes on retry while a persistently dead link still fails the build.

## User Stories

1. As a contributor, I want a push during a CI run not to fail the build, so that a green build stays green.
2. As a contributor, I want an older run of my branch cancelled when I push again, so that I'm not waiting behind obsolete runs.
3. As the operator, I want `main` builds never cancelled, so that every merge still publishes its image.
4. As the operator, I want CI to say "disk nearly full" when it is, so that I don't chase a misleading failure.
5. As the operator, I want CI never to delete anything on the production host by itself, so that a guard cannot cause harm.
6. As a contributor, I want the link check not to fail on a transient connection error, so that unrelated PRs and `main` stay green.

## Implementation Decisions

- **Baseline step.** Keep the existing guard that only acts when measured coverage rose, and keep `[skip ci]`. Before pushing, fetch the remote branch; if its tip is not the commit this run tested, skip the raise (the measured coverage belongs to an older commit, and raising the baseline from it could fail the next run). Pushing to that branch is only attempted when it has not moved; if that push is still rejected, log a warning and exit successfully. The raise stays on every branch, as today, to keep the change minimal.
- **Concurrency.** Both workflows get a `concurrency` block. For `main`, the group is keyed on the commit SHA and `cancel-in-progress` is false, so no `main` run is cancelled and a newer pending `main` run cannot replace an older pending one (which would skip an image build). For every other ref, the group is the workflow plus ref and `cancel-in-progress` is true. A cancelled non-main run may be mid image push to the `dev` tag; that is harmless because the next run republishes it. The bot baseline commit carries `[skip ci]`, so it starts no run.
- **Disk guard.** The first step of each self-hosted job, in plain bash, with the threshold as a named variable (default 5 GB). Below it, the step prints `df -h /` and `docker system df`, names the usual culprits (unused docker volumes, build cache), and fails. It must contain no `rm` or `prune`.
- **Link check.** The tool only retries HTTP 429 and its `timeout` is per request, so a dropped or refused connection (`Status: 0`) is never retried, and a longer timeout or different headers cannot change that. The flake could not be reproduced locally (0 of 8 runs), so there is no evidence for any header or timeout tweak and none is made. Instead the "Check markdown links" step in the shared composite action runs the whole check up to three times, with a short delay between attempts, and fails only if every attempt fails. A transient connection failure therefore passes on retry; a genuinely dead link fails all three attempts and still fails the build. The local pre-commit hook is unchanged. `.markdown-link-check.json` is unchanged.

## Testing Decisions

- GitHub Actions behaviour cannot be exercised locally. Structural tests in `scripts/tests` (prior art: `test_deployment_layout.py`) parse the workflow YAML and pin the intended structure: the concurrency expression text, the disk guard as the first step with a threshold and no deleting commands, and the baseline step fetching and skipping when the branch moved and not failing the job. The guard, the baseline step and the link-check step are also executed for real under `bash -eo pipefail` against fake binaries (and, for the baseline, a throwaway git repository), which is where their behaviour is tested: a transient link-check failure passes on retry, a persistent one fails after three attempts, and a clean check runs once.
- `actionlint` and `shellcheck` (existing pre-commit hooks) validate syntax. The key tests are mutation-checked by temporarily reverting the workflow change.
- The real behaviour is only proven by the next CI runs; the PR lists what to look for.

## Out of Scope

- Any automatic cleanup on the runner (no prune or delete in CI), and a scheduled prune workflow.
- Restricting the baseline raise to `main`, moving the build off the production host, or changing the build or push steps.
- The cost-forecast policy (#575) and the other Phase D items.

## Further Notes

- Related incidents: the baseline race on PR #563, the volume leak and full disk fixed in #573, and the `Status: 0` link failure on `main` for the #567 merge.

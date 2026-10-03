# Issues: chore-ci-hardening

## ci: tolerant baseline step, cancel superseded runs, low-disk guard — [#577](https://github.com/mholubinka1/home-monitoring/issues/577)

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5

### What to build

Harden the two workflows against the incidents seen this week on the single
self-hosted ARM64 runner. (1) The "Raise coverage baseline" step must never
fail the job: skip the raise when the remote branch has moved on from the
commit this run tested, and treat a push that still cannot land as a warning
(keep the only-when-coverage-rose guard and `[skip ci]`). (2) Add
`concurrency` to both workflows so a new push to a branch cancels that
branch's older runs, but never cancel `main` runs (key the group on the
commit SHA for `main`, `cancel-in-progress` false there). (3) Add a first
step to each self-hosted job that fails fast with a clear message when root
free space is below a named threshold (default 5 GB), printing `df -h /` and
`docker system df`, and deleting nothing. Pin the structure with tests in
`scripts/tests` that parse the workflow YAML.

### Acceptance criteria

- [ ] Given both workflows, `concurrency` cancels superseded runs for
      non-main refs and never cancels (or replaces) runs on main.
- [ ] Given each self-hosted job, the first step is a disk guard with a named
      threshold that fails with a clear message and contains no delete or
      prune command.
- [ ] Given the baseline step, it skips the raise when the remote branch has
      moved, and a push that cannot land warns without failing the job.
- [ ] The structural tests fail when each of these changes is reverted
      (mutation-checked), and actionlint/shellcheck pass.
- [ ] The PR states what the next CI runs should show.

---

## ci: steady the flaky markdown link check (Status 0) — [#578](https://github.com/mholubinka1/home-monitoring/issues/578)

**Blocked by**: None

**User stories**: 6

### What to build

Make the markdown link check steady against connection-level failures while
still checking the link. `markdown-link-check` intermittently reports
`Status: 0` for `https://docs.octopus.energy/graphql/reference/mutations/` in
`.agent-docs/research/octopus-billing-period-api.md`; direct requests return
200 in about 0.3 s every time, and it failed Code Quality on `main` for the
merge of PR #567. The config retries only on HTTP 429. Investigate with repeated
runs and choose the cheapest robust fix that keeps checking the link (larger
timeout, a browser-like User-Agent via `httpHeaders`, a retry change); an
ignore entry for that one URL is a last resort and needs a comment. Pin the
chosen setting with a test in `scripts/tests`.

### Acceptance criteria

- [ ] The chosen fix is evidenced by repeated local runs of the checker
      against that link.
- [ ] The link remains checked, unless an ignore entry is the documented last
      resort.
- [ ] A test pins the chosen setting in `.markdown-link-check.json`.

---

# Issues: chore-retire-rename-runbook

## chore: retire RENAME_RUNBOOK.md now the cutover is done — [#588](https://github.com/mholubinka1/home-monitoring/issues/588)

**Blocked by**: None

### What to build

Delete `deployments/mariadb/RENAME_RUNBOOK.md` (the 2026-10-01 cutover is done and
the file is stale), keep it reachable through a permalink into the repository
history, and repoint everything that referred to it.

### Acceptance criteria

- [ ] `deployments/mariadb/RENAME_RUNBOOK.md` no longer exists.
- [ ] ADR-0022, ADR-0024 and `deployments/CUTOVER_RUNBOOK.md` link to the
      runbook's last version in git history instead of the deleted path.
- [ ] No live file (ADRs, runbooks, SQL script, tests) refers to the deleted
      path; the comments that mention it say it is retired.
- [ ] The layout test's retired-names check no longer exempts the deleted
      file, and still passes.
- [ ] Historical specs and issue files are left as the records they are.

## Follow-on, not in this PR — [#589](https://github.com/mholubinka1/home-monitoring/issues/589)

Drop the empty `octopus` database and the old `octopus-monitoring` Docker Hub
tag. That step only lived in the deleted runbook, so it is tracked as its own
issue; each action needs explicit confirmation.

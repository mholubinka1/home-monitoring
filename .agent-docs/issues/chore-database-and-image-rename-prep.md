# Issues: chore-database-and-image-rename-prep

## Record the Docker Hub image target name (ADR)

**GitHub issue**: #542

**Blocked by**: None

**User stories**: 4

### What to build

Write an ADR deciding the Docker Hub image's target name, replacing the stale `mholubinka1/octopus-monitoring`. Recommend `mholubinka1/octopus-app`, mirroring the already-correctly-named `mholubinka1/hive-app` image and the `octopus-app` package/directory name, over a repo-wide namespace prefix (Docker Hub's account already scopes it). Cross-link ADR-0022 (the database rename decision this pairs with at cutover) and note both changes land on the Pi together in one confirmed cutover window, not staggered.

### Acceptance criteria

- [x] New ADR under `.agent-docs/adr/` records the target image name and the rejected alternative (namespace prefix), with rationale
- [x] ADR cross-links ADR-0022 and states both renames execute together at the confirmed Pi cutover
- [x] No code, CI, or compose changes in this slice — `.github/workflows/ci-arm64.yml` and `deployments/octopus-app/docker-compose.yml` still reference `octopus-monitoring`

---

## Write and test the database rename migration script

**GitHub issue**: #543

**Blocked by**: None

**User stories**: 1, 6

### What to build

A standalone SQL script (`scripts/rename_database.sql`) that renames the `octopus` database to `home_monitoring` with zero data loss, guarded (via a stored procedure raising `SIGNAL`) against running twice, against a missing source, or against a source missing any of the eleven expected tables — all checked *before* any DDL runs, so a rejected run never leaves a dangling `home_monitoring` database that would falsely block a later, real retry. The eleven tables (`consumption`, `agreement`, `product`, `product_rate`, `daily_consumption_summary`, `agile_forecast`, `cost_forecast`, `heating_status`, `weather_observation`, `weather_forecast`, `job_run`) are verified directly against all three packages' SQLAlchemy models, not assumed. Requires MariaDB root (documented in the runbook, `deployments/mariadb/RENAME_RUNBOOK.md`), since `USE mysql`, `CREATE DATABASE`, and a cross-database `RENAME TABLE` all exceed an app user's own database-scoped grant. Leaves `octopus` in place empty as the rollback path. Verified by a real automated pytest test (`scripts/tests/test_rename_database.py`) against a throwaway `mariadb:latest` container started via plain `docker` subprocess calls — six Given/When/Then scenarios (successful migration with row-count parity, already-migrated rejection, missing-source rejection with no dangling database left behind, successful recovery on retry once the source exists, rejection when run as an under-privileged app user, and rejection when a table is missing from an otherwise-present source), added one at a time, red before green each time. Every table gets a distinct seeded row count so a regression losing rows from any one of them is caught, not just the couple a smaller seed would happen to cover. Skipped gracefully when docker isn't available locally; runs for real in CI.

### Acceptance criteria

- [x] Script rejects running if `home_monitoring` already exists, `octopus` does not exist, or `octopus` is missing any of its eleven expected tables, via a precondition guard that runs *before* any DDL — a rejected run leaves no dangling `home_monitoring` database behind
- [x] All eleven tables land in `home_monitoring` with identical row counts to their `octopus` originals, verified individually per table (not just a couple of representative ones)
- [x] `octopus` database still exists afterward, empty of tables
- [x] Six scenarios pass as real pytest tests against a real MariaDB container, added one at a time (red-green) rather than written in bulk upfront, including recovery on retry, the root-privilege requirement, and the missing-table case
- [x] Test is skipped gracefully (not failed) when docker is unavailable
- [x] `scripts/tests` is discovered by the root `pyproject.toml`'s pytest `testpaths`
- [x] No change to any live database — this slice only adds the script, its test, and CI wiring

---

## Write the Pi cutover runbook

**GitHub issue**: #545

**Blocked by**: #542, #543

**User stories**: 2

### What to build

An operator-facing runbook covering the full confirmed-cutover procedure end-to-end: stop both app containers, take a backup and verify it (exit status checked, expected table count confirmed in the dump), run the migration script, verify exact row counts, flip CI's published image name and the Pi's own compose image reference, update both apps' live config, restart containers, confirm `job_run` rows under the new name, and the rollback path. Flags [#549](https://github.com/mholubinka1/home-monitoring/issues/549) (discovered during review: every model hardcodes `schema="octopus"`, so the config-update step alone doesn't retarget the apps) as a hard prerequisite before the config/image section — a section-level blocker, not a caveat buried in prose.

### Acceptance criteria

- [x] Runbook lists explicit pre-checks (both containers stopped, backup taken and verified — exit status and expected table count, not just redirected) before any destructive step
- [x] Runbook's happy path references the migration script from the prior slice by its actual path
- [x] Runbook covers both renames (database and image) as one combined session, matching ADR-0022's "together, not staggered" decision
- [x] Runbook includes a rollback section covering both the database and the image/config reference
- [x] Runbook is reviewed for accuracy against the actual scripts/paths it references (no placeholder paths left in)

---

## Update reference compose/init.sql to the target database name

**GitHub issue**: #544

**Blocked by**: None

**User stories**: 3

### What to build

Update `data/mariadb/init.sql`'s `CREATE DATABASE IF NOT EXISTS octopus;` to `home_monitoring`, and `deployments/mariadb/docker-compose.yml`'s `MARIADB_DATABASE: octopus` to `home_monitoring`.

### Acceptance criteria

- [x] `data/mariadb/init.sql` creates `home_monitoring`, not `octopus`
- [x] `deployments/mariadb/docker-compose.yml`'s `MARIADB_DATABASE` is `home_monitoring`
- [x] `docker compose -f deployments/mariadb/docker-compose.yml config` and the combined `deployments/docker-compose.yml` still validate
- [x] No other compose file changes in this slice
- [x] Existing test suite passes unchanged (no application code touched)

---

## Update docs to reflect prep-complete, cutover-still-open status

**GitHub issue**: #546

**Blocked by**: #542, #543, #544, #545

**User stories**: 3, 4

### What to build

Update `.agent-docs/context.md`'s deferred-rename mentions and ADR-0022 to reference the new ADR-0024 and the completed prep artifacts (script, test, runbook), without claiming the live cutover has happened.

### Acceptance criteria

- [x] `.agent-docs/context.md`'s two deferred-rename mentions reference the new ADR and the completed prep artifacts
- [x] ADR-0022 links to the migration script and runbook by path
- [x] Neither doc claims the cutover itself has happened

---

## [Blocked — do not start without explicit confirmation] Execute the Pi cutover

**GitHub issue**: #547

**Blocked by**: Explicit user confirmation of the Pi cutover window, and [#549](https://github.com/mholubinka1/home-monitoring/issues/549) (not by any issue above technically otherwise, but do not start until #542, #543, #544, #545, #546 are merged)

**User stories**: 5

### What to build

Nothing until the user explicitly confirms the cutover window. When confirmed: run the runbook against the live Pi.

### Acceptance criteria

- [x] Not started until the user explicitly confirms the cutover window
- [ ] Not started until #549 is closed
- [ ] Backup taken and verified before any destructive step
- [ ] Live Pi database successfully renamed with verified row counts, `octopus` left intact as a rollback path
- [ ] CI publishes the new image name; Pi's live compose file updated to pull it
- [ ] Both apps confirmed writing to `home_monitoring` post-cutover
- [ ] `octopus` database and old image tag dropped only as a later, separately-confirmed step — not part of this issue

---

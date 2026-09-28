# Prepare the deferred `octopus` → `home_monitoring` database and Docker Hub image renames

## Problem Statement

The `apps`/`libs`/`data`/`deployments` restructure (ADR-0022, `.agent-docs/context.md`'s "Home Monitoring Restructure" entry) deliberately deferred two renames until a future, explicitly-confirmed Pi cutover: the shared MariaDB database (`octopus` → `home_monitoring`) and the Docker Hub image (`mholubinka1/octopus-monitoring` → a name that fits two apps). Both are still open — the database still runs as `octopus` on the live Pi, and CI still pushes `mholubinka1/octopus-monitoring`. Nothing in the repo currently describes *how* either rename will actually be carried out, so when the cutover is eventually confirmed there is no reviewed, tested script or runbook ready to run — the work would start from scratch under time pressure, against a database with real data.

## Solution

Prepare everything the eventual cutover needs — a reviewed and automatically-tested migration script, an operator runbook, and the target-state config/compose/doc changes — without touching the live Pi database or changing what CI publishes today. The cutover itself (running the migration against real data, flipping CI's published image name, updating the Pi's own `docker-compose.yml`) stays a separate, explicitly-confirmed step, tracked as its own follow-on work and blocked until that confirmation happens.

## User Stories

1. As the operator running the eventual Pi cutover, I want a tested migration script that renames the `octopus` database to `home_monitoring` with zero data loss, so that I can run one reviewed command instead of improvising `RENAME TABLE`/dump-restore steps live.
2. As the operator, I want a runbook describing pre-checks, the exact command sequence, and rollback, so that the cutover is a supervised, repeatable procedure rather than a one-off judgment call.
3. As a maintainer reading `data/mariadb/init.sql` or `deployments/mariadb/docker-compose.yml`, I want them to already reflect the target `home_monitoring` name, so that the reference compose files (already documented as "not yet deployed" to the Pi) describe the destination state, not a name we're mid-way through abandoning.
4. As a maintainer, I want the Docker Hub image's target name decided and documented (ADR), so that the CI change to publish under it is a small, reviewed, mechanical follow-on rather than an open naming question at cutover time.
5. As a maintainer, I want the actual cutover steps (run the migration on the Pi's real data, flip CI's published tag, update the Pi's live `docker-compose.yml`) kept out of this slice and tracked as explicitly-blocked follow-on issues, so that merging this prep work never silently changes production behaviour.
6. As a future contributor, I want the migration script's behaviour verified by an automated test that runs in CI, not just a one-off manual docker session, so that a later edit to the script (e.g. a new table Schema Sync grows) can't silently regress it.

## Implementation Decisions

- **Database rename script**: `scripts/rename_database.sql` — verifies the source database exists and the target does not, creates `home_monitoring`, moves every table Schema Sync currently owns (`consumption`, `agreement`, `product`, `product_rate`, `daily_consumption_summary`, `agile_forecast`, `cost_forecast`, `heating_status`, `job_run`) via `RENAME TABLE`, and leaves the empty `octopus` database in place (dropped only as an explicit later step, never by this script) so the migration is trivially reversible by renaming back. Rejects running (via a guarded stored procedure raising `SIGNAL`) rather than silently no-op-ing if run twice.
- **Runbook**: `deployments/mariadb/RENAME_RUNBOOK.md` covering: stop both app containers first (schema sync must not run mid-rename), take a `mariadb-dump` backup regardless of the rename approach, run the script, verify row counts match pre/post per table, update both apps' `config.yml`/`hive-config.yml` `mariadb.database` value on the Pi, restart the containers, confirm `job_run` rows are being written under the new name, and the rollback path (rename back, restore config).
- **Reference compose/init.sql target state**: update `data/mariadb/init.sql`'s `CREATE DATABASE IF NOT EXISTS octopus;` to `home_monitoring`, and `deployments/mariadb/docker-compose.yml`'s `MARIADB_DATABASE: octopus` to `home_monitoring`. These already carry the "reference copy, not yet deployed to the Pi" note, so updating them to the target name now does not affect the live Pi, which still runs its own separately-synced compose file pointing at `octopus`.
- **Docker Hub image name decision**: record a new ADR deciding the target name — `mholubinka1/octopus-app` (dropping the stale "monitoring" framing, mirroring the already-correctly-named `mholubinka1/hive-app` image, and matching the `octopus-app` package/directory name) over introducing a repo-wide namespace prefix (e.g. `home-monitoring/octopus-app`), since Docker Hub namespacing by account already scopes it and a prefix adds no information the account doesn't already provide.
- **What does NOT change in this slice**: `.github/workflows/ci-arm64.yml`'s `DOCKER_IMAGE` value stays `octopus-monitoring` (changing it immediately stops updates to the image the live Pi's Watchtower currently tracks); `deployments/octopus-app/docker-compose.yml`'s `image:` stays `mholubinka1/octopus-monitoring:latest` for the same reason; no command is run against the live Pi database.
- **Docs**: update `.agent-docs/context.md`'s two "deferred rename" mentions (the `octopus` database glossary entry and the "Home Monitoring Restructure" entry's Docker Hub line) to point at the new ADR and note that prep (script/runbook/target-name decision) is done, cutover itself is still open. Update ADR-0022 with a note that the migration script and runbook now exist at their real paths, ready for the confirmed cutover.

## Testing Decisions

- The migration script gets a real automated pytest test (not just a manual verification session), since it's committed code a later change (e.g. Schema Sync growing a new table) could silently regress. The test seam is the script itself run against a real, disposable MariaDB instance — the same seam the script actually operates on, not a mock of `RENAME TABLE` semantics. Implementation: a pytest fixture starts a throwaway `mariadb:latest` container via `docker` (no new library dependency — plain `docker run`/`docker exec` subprocess calls, matching how `deployments/mariadb/docker-compose.yml` already runs the same image), tears it down after each test.
- Scenarios (Given/When/Then, one tracer-bullet test at a time):
  1. Given a seeded `octopus` database with all nine tables and representative rows, when the script runs, then `home_monitoring` has all nine tables with matching row counts and `octopus` still exists, empty.
  2. Given an already-migrated instance (`home_monitoring` exists), when the script runs again, then it fails with a clear error and does not corrupt either database.
  3. Given no `octopus` database at all, when the script runs, then it fails with a clear error naming the missing source and leaves no dangling `home_monitoring` database behind (a guard check before any DDL, not a natural DDL error, since `CREATE DATABASE` alone would otherwise succeed before the later `RENAME TABLE` failed).
  4. Given a rejected run from scenario 3, when `octopus` is then created and the script is run again, then it succeeds — a rejected run must not permanently block a later, real retry.
  5. Given the script run as an app user scoped only to `octopus` (the shape `MARIADB_USER` gets in production), when the script runs, then it fails — `USE mysql`, `CREATE DATABASE`, and a cross-database `RENAME TABLE` all need root, which `deployments/mariadb/RENAME_RUNBOOK.md` documents explicitly.
  6. Given `octopus` exists but is missing one of the nine expected tables, when the script runs, then it fails with a clear error and leaves no dangling `home_monitoring` database behind — `octopus` existing isn't sufficient on its own; every table it should hold is checked too.
- The test is skipped, not failed, when `docker` isn't available on `PATH` or the daemon isn't reachable (`pytest.mark.skipif`), so contributors without docker and CI environments that lack it aren't broken by a script test that only matters once, on the Pi. CI's self-hosted ARM64 runner already has docker available (it builds/pushes images in the same job), so the test runs there for real.
- New test file lives at `scripts/tests/test_rename_database.py`; `scripts/tests` is added to the root `pyproject.toml`'s `[tool.pytest.ini_options] testpaths`. It is not added to `[tool.coverage.run] source` — it's a script test, not application code, and doesn't affect the 80% coverage gate.
- No application code changes in this slice, so the existing `apps/`/`libs/` suite passes unchanged — that's the acceptance bar for everything except the new script test.
- `docker compose -f deployments/mariadb/docker-compose.yml config` and the combined `deployments/docker-compose.yml` still validate after the `init.sql`/compose name changes.

## Out of Scope

- Running the migration against the live Pi database.
- Changing `.github/workflows/ci-arm64.yml`'s published image name, or `deployments/octopus-app/docker-compose.yml`'s `image:` value.
- Updating the Pi's own separately-synced `docker-compose.yml` at `/home/pi/git/pi-desktop/docker/docker-compose.yml`.
- Dropping the `octopus` database once migrated — that's a later, separate confirmed step after the new name has been running successfully for a while.
- Any other Wayfinder map (#490) item not related to these two renames.

## Further Notes

Both renames are gated on the same real-world event (an explicitly-confirmed Pi cutover window with accepted brief downtime, per ADR-0022) — they should land on the Pi together in one supervised session, not staggered, since the database and image name changes are otherwise independent of each other technically. This spec's issues split cleanly into "prep" (safe to merge to `main` any time, done here) and "execute cutover" (explicitly blocked, opened as a follow-on issue with no target date, closed only once the user confirms the cutover window). GitHub issues #542-547 already exist from an earlier pass at this same spec and are being reused rather than duplicated.

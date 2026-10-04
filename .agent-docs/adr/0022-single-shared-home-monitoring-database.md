# Single shared `home_monitoring` database, not per-app databases

`hive-app` already wrote its `heating_status` table into the same database as `octopus-app` (`octopus`), and `job_run` was already a single table both apps wrote to, with a comment flagging the ownership question as unresolved (Wayfinder #492). Once `libs/common` (ADR-0020) centralized `job_run` and Schema Sync ownership in one place, splitting the database in two stopped making sense — a single cross-app table only works cleanly against one database, not two. The decision: keep one shared MariaDB database on the one MariaDB instance, and rename it from `octopus` to `home_monitoring` to reflect that it now holds both apps' data, not just Octopus Energy's. Each app's Schema Sync (via `libs/common`) still only diffs its own SQLAlchemy models against the live schema — the database is shared, not the schema-sync run.

The rename is a one-time migration executed during the Pi cutover window (dump/restore, or `RENAME TABLE` into a freshly created `home_monitoring` database) — the business constraint for this whole restructure is zero data loss with brief downtime accepted, so this happens as a deliberate, supervised step rather than automatically at app startup.

The migration script (`scripts/rename_database.sql`, tested by `scripts/tests/test_rename_database.py`) and the operator runbook (`deployments/mariadb/RENAME_RUNBOOK.md`) are written and verified against a throwaway MariaDB instance; the Docker Hub image rename it pairs with is decided in [ADR-0024](0024-docker-hub-image-name-octopus-app.md). One prerequisite surfaced during review of that runbook remains open before the cutover can actually proceed: every SQLAlchemy model hardcodes `schema="octopus"` rather than deriving it from config, so a config-only database-name change does not retarget the apps on its own ([#549](https://github.com/mholubinka1/home-monitoring/issues/549)).

> **Since resolved.** The rename was executed during the live Pi cutover on 2026-10-01
> (see the `home_monitoring` entry in [context.md](../context.md)), and the open prerequisite above
> (#549, closed) was resolved by [ADR-0025](0025-schema-translate-map-for-config-driven-mariadb-schema.md).
> The paragraphs above are kept as written, as the record of the decision at the time.

## Considered Options

- **Per-app databases on the same instance** — rejected: `job_run` (ADR-0020) is deliberately one shared table; splitting the database would force either duplicating it back across two databases (reintroducing the exact drift risk ADR-0020 removed) or cross-database queries, neither of which is worth it for two containers on one Pi.
- **Keep the `octopus` name** — considered as lower-risk (no migration needed), but rejected in favor of the rename: the name now permanently misdescribes its contents, and the business constraint accepts brief downtime, so the migration risk is small relative to the ongoing confusion of "why is heating data in a database called octopus."

# Make MariaDB schema config-driven instead of hardcoded `octopus`

## Problem Statement

The operator preparing to rename the shared MariaDB database from `octopus` to `home_monitoring` ([ADR-0022](../adr/0022-single-shared-home-monitoring-database.md)) cannot actually retarget `octopus-app` or `hive-app` by updating `config.yml`'s `mariadb.database` value, as the [rename runbook](../../deployments/mariadb/RENAME_RUNBOOK.md) currently instructs. Every SQLAlchemy model across `libs/common`, `apps/octopus-app`, and `apps/hive-app` hardcodes `schema="octopus"` in its table definition. SQLAlchemy uses that literal to fully qualify every compiled query, overriding whatever database the connection URI (built from `settings.database`) points at. After a config-only rename, the apps would keep reading and writing `octopus.*` — silently stale against the renamed database — and Schema Sync could recreate empty tables back in `octopus`.

Discovered during Copilot review of [#548](https://github.com/mholubinka1/home-monitoring/pull/548) and tracked as [#549](https://github.com/mholubinka1/home-monitoring/issues/549); it blocks the still-open Pi cutover issue [#547](https://github.com/mholubinka1/home-monitoring/issues/547).

## Solution

A database rename becomes a config change plus a data migration — never a source change. Changing `mariadb.database` in either app's config retargets every query (ORM reads/writes, raw Core-table reads, and Schema Sync's `CREATE TABLE`/`ALTER TABLE`/`CREATE INDEX` DDL) to the configured database, with no edits required to any model file.

## User Stories

1. As the operator executing the deferred Pi cutover ([#547](https://github.com/mholubinka1/home-monitoring/issues/547)), I want changing `mariadb.database` in `config.yml`/`hive-config.yml` to be sufficient to retarget an app at the renamed database, so that the runbook's config-update step actually works as documented.
2. As a maintainer reading `libs/common/common/mariadb/client.py` or any model file after the rename ships, I want the still-present `schema="octopus"` literals to be clearly explained (via ADR and inline comment), so that I don't mistake them for an incomplete rename.
3. As a maintainer running the test suite, I want a real-MariaDB-backed test proving the configured database is actually used for reads, writes, and Schema Sync DDL, so that this guarantee is verified against real MySQL/MariaDB schema-qualification semantics, not assumed from reading SQLAlchemy documentation.

## Implementation Decisions

- **Single change point**: `libs/common/common/mariadb/client.py`'s `SessionBuilder.__init__` sets `self.engine = create_engine(uri).execution_options(schema_translate_map={"octopus": settings.database})`. No other file changes behavior.
- **Every existing `schema="octopus"` literal stays exactly as-is, permanently** — 11 occurrences in `__table_args__` across `libs/common/common/mariadb/model.py`, `apps/octopus-app/octopus_app/data/mysql/model.py`, and `apps/hive-app/hive_app/data/mysql/model.py`, plus 2 more in `apps/octopus-app/octopus_app/data/mysql/client.py`'s standalone `weather_observation_table`/`weather_forecast_table` Core `Table` objects. These literals become fixed `schema_translate_map` source keys, not database names.
- **Recorded as [ADR-0025](../adr/0025-schema-translate-map-for-config-driven-mariadb-schema.md)** — explains why the literals never change, since a future reader seeing `schema="octopus"` after the Pi's database is renamed would otherwise reasonably assume the rename was incomplete.
- **`.agent-docs/context.md` updated** — the `octopus` database glossary entry now points at ADR-0025 as the resolution to the previously-open prerequisite; a new `schema` glossary entry disambiguates SQLAlchemy/MySQL "schema" (a synonym for database in this stack) from the unrelated **Schema Sync** mechanism.
- **Backwards compatible by construction**: today's deployments have `settings.database == "octopus"`, so `schema_translate_map={"octopus": "octopus"}` is an identity mapping — zero behavior change until an operator actually changes the config value.
- **No runtime guard added** for `octopus-app` and `hive-app`'s separate config files agreeing on the same `database` value — that stays operator responsibility, unchanged from today, consistent with the existing runbook.
- **Out of scope for this issue**: executing the actual Pi cutover ([#547](https://github.com/mholubinka1/home-monitoring/issues/547), still blocked on an explicit operator-confirmed downtime window).

## Testing Decisions

- **Seam**: `MariaDBClientBase`/`SessionBuilder`, constructed against a real, throwaway `mariadb:latest` Docker container — this repo's established pattern for verifying real MySQL/MariaDB schema-qualification semantics (`scripts/tests/conftest.py`), not mocks. `libs/common/tests/` currently only tests against an in-memory SQLite engine (`libs/common/tests/conftest.py`'s `mariadb_client` fixture), which cannot exercise real schema-qualification behavior — SQLite has no schema/database concept.
- **New fixture**: a `mariadb_container` Docker fixture added to `libs/common/tests/conftest.py`, following the pattern in `scripts/tests/conftest.py` (start container, wait for "ready for connections" twice in logs, `--rm` teardown) but not imported from it — `libs/common` gains no dependency on `scripts/`.
- **Assertions** (against a container database explicitly *not* named `octopus`, e.g. `home_monitoring_test`):
  - `MariaDBClientBase.__init__`'s Schema Sync run creates tables in the configured database, not `octopus`.
  - An ORM write (`record_job_run`) and read (`has_successful_job_run`) round-trip correctly against the configured database.
  - A raw Core `Table` query, matching the shape of `octopus-app`'s `weather_observation_table` pattern, also resolves against the configured database.
  - Querying `octopus` directly (via `run_sql`, matching `scripts/tests/conftest.py`'s helper) shows no tables were created there.
- Skipped, not failed, when Docker isn't available, matching `scripts/tests/conftest.py`'s `requires_docker` marker.

## Out of Scope

- Executing the Pi cutover itself ([#547](https://github.com/mholubinka1/home-monitoring/issues/547)).
- A runtime check that `octopus-app` and `hive-app`'s configured `database` values match.
- Renaming any `schema="octopus"` literal.
- Extracting a shared Docker-fixture utility between `scripts/tests/` and `libs/common/tests/`.

## Further Notes

`libs/common/tests/conftest.py`'s existing `mariadb_client` SQLite fixture already uses `schema_translate_map={"octopus": None}` to run these same models against SQLite — direct precedent in this codebase that the mechanism works against these exact models, ahead of any new container test.

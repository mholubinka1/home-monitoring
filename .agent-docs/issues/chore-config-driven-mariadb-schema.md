# Issues: chore-config-driven-mariadb-schema

## Make MariaDB schema config-driven via schema_translate_map (#551)

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

`SessionBuilder` (`libs/common/common/mariadb/client.py`) sets `schema_translate_map={"octopus": settings.database}` on its engine's execution options, so every compiled query — ORM reads/writes, raw Core-table reads, and Schema Sync's `CREATE TABLE`/`ALTER TABLE`/`CREATE INDEX` DDL — resolves against the configured database instead of the literal `"octopus"` schema baked into every model. No model file changes; every existing `schema="octopus"` declaration (11 across the three packages' model files, plus 2 in `octopus-app`'s standalone weather-table Core objects) stays as-is, now serving as a fixed translation-map source key.

Add a real-`mariadb:latest`-container test proving this end-to-end against a database explicitly not named `octopus` (e.g. `home_monitoring_test`): Schema Sync creates tables there (not in `octopus`), an ORM write/read round-trips correctly, and a raw Core-table-style query also resolves there. New `mariadb_container` Docker fixture added to `libs/common/tests/conftest.py`, following the pattern already in `scripts/tests/conftest.py` (start container, wait for "ready for connections" twice in logs, `--rm` teardown, skip-not-fail when Docker is unavailable) without importing from it.

### Acceptance criteria

- [ ] `SessionBuilder` sets `schema_translate_map={"octopus": settings.database}` on its engine.
- [ ] A real-container test asserts Schema Sync's table/column/index creation lands in the configured database, not `octopus`.
- [ ] A real-container test asserts an ORM write + read round-trips against the configured database.
- [ ] A real-container test asserts a raw Core `Table` query (matching `octopus-app`'s weather-table pattern) resolves against the configured database.
- [ ] A real-container test asserts `octopus` itself has no tables created in it when the configured database is something else.
- [ ] Existing `libs/common/tests/conftest.py` SQLite-backed tests (`mariadb_client` fixture) still pass unchanged.
- [ ] No edits to any model file or to `octopus-app/client.py`'s weather-table declarations.
- [ ] `.agent-docs/context.md`'s `octopus` database entry and new `schema` entry, and [ADR-0025](../adr/0025-schema-translate-map-for-config-driven-mariadb-schema.md), are in place (already written during design).

---

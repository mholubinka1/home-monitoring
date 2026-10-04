---
status: accepted
---

# Additive-only, hand-rolled schema sync on app startup

`data/mariadb/init.sql` only runs when MariaDB's data directory is empty, so on the Pi's already-populated volume it never re-executes — every schema change since the app first deployed has required a manual DDL step nobody remembered to do consistently. We decided to have the app synchronize its own schema automatically on every startup, rather than adopt a versioned migration framework (Alembic or similar), because this is a single-user deployment with one target and no rollback requirement — the operational weight of a full migration tool isn't earned here. The sync is restricted to additive changes only: creating missing tables (via SQLAlchemy's `metadata.create_all(engine, checkfirst=True)`), then adding missing columns and creating missing indexes on existing tables (each via a small hand-rolled diff against `sqlalchemy.inspect()`). It never drops a table, column, or index, and never alters an existing column's type or nullability — that class of change stays a deliberate, manual, one-off action outside the tool, because a bug in an automated destructive migration is the one failure mode that can't be undone on a database holding the only copy of collected consumption/pricing history.

This also collapses `model.py` (formerly `sql_models.py`) and `data/mariadb/init.sql` into a single source of truth: `init.sql` now only creates the `octopus` database (since renamed `home_monitoring`, see ADR-0022), and every table definition lives in `model.py` alone. The two files drifting apart was a recurring review cost before this change.

## Consequences

- Widening an existing column's constraint (e.g. adding `NOT NULL` to a column that already has data) is permanently out of scope for the automated tool. Every future feature's schema additions must be genuinely additive (new tables/columns, nullable or defaulted) or handled by hand. This includes adding a brand new `NOT NULL` column with no `server_default` to a table that already has rows — MariaDB will reject the `ALTER TABLE ... ADD COLUMN` outright, which is the fail-fast behaviour working as designed, not a bug to guard against in code.
- A schema-sync failure at startup is fatal by design (fail fast, matching the existing `get_settings` startup-failure precedent) — the app will not run against a schema it couldn't verify or extend.
- This was verified against SQLite in tests; real MariaDB-specific DDL behavior has not been exercised in this environment (Docker Desktop unavailable here) and is worth a manual check after first deploy.

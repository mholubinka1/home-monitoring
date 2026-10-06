# Shared `common` library for cross-app infrastructure code

Once `hive-app` existed alongside `octopus-app`, most of `app/common/` and `app/data/mysql/` turned out to be genuinely app-specific (retry strategy, exceptions, config schema all diverge) except for two things: logging setup (identical structure, differs only by logger name) and MariaDB session/engine/schema-sync plumbing, which was hand-duplicated with an explicit "keep both in sync" comment. Rather than accept that duplication indefinitely or fully generalize every shared-looking module, only the genuinely generic pieces — logging config, MariaDB engine/session context managers, the additive-only Schema Sync mechanism (ADR-0005), and the tables shared by both apps (`job_run`, which both write to; since ADR-0028, also `account_postcode`, written by octopus-app and read by hive-app) — move into a new top-level `libs/common/` package that both apps depend on. Domain-specific models and CRUD (consumption, pricing, heating status, etc.) stay owned by their own app.

`libs/common/` sits outside `apps/` because it has no entrypoint or container of its own — `apps/` is reserved for things that build a deployable image.

## Considered Options

- **Full independence, no shared library** — closest to the status quo; rejected because it leaves Schema Sync and `job_run` duplicated indefinitely across two files that must be hand-kept-in-sync, which is exactly the kind of drift Schema Sync (ADR-0005) was built to avoid in the first place.
- **One large shared package covering all data-access code** — rejected because most of `app/data/` genuinely doesn't generalize (Octopus-specific retrieval, pricing, cost forecasting have no hive-app equivalent); forcing it into a shared package would just move app-specific code into a place that implies it's generic.

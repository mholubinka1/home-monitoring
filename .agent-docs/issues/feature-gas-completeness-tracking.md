# Issues: feature-gas-completeness-tracking

> Work complete — merged in [PR #660](https://github.com/mholubinka1/home-monitoring/pull/660). GCT-1's live database check was done after the 2026-10-10 deploy: Schema Sync added `half_hour_count` and the startup backfill filled counts back to 2024-10-12 for both energies.

## GCT-1 · Record the readings behind each daily total and define a complete gas day — [#646](https://github.com/mholubinka1/home-monitoring/issues/646)

**Blocked by**: None

**User stories**: 1, 2, 4

### What to build

`daily_consumption_summary` gets a nullable count of half-hour readings per London day (both energies), set by the summary job from the raw rows, and a single shared definition of a complete gas day: total above zero and count equal to the day's expected half-hours (46, 48 or 50), with an unknown count treated as complete unless the total is zero and reported as unverified.

### Acceptance criteria

- [x] Given 48 raw half-hours for a London day, then the summary stores the total and a count of 48 and the day is complete.
- [x] Given 36 of 48, then the day is not complete.
- [x] Given a spring-forward day with 46 and an autumn day with 50, then each is complete.
- [x] Given 48 readings that are all zero, then the day is not complete.
- [x] Given an unknown count and a positive total, then the day is usable and reported as unverified; with a zero total it is not usable.
- [x] Schema Sync adds the column to the existing table, existing rows reading NULL (SQLite test in octopus-app; the real MariaDB fixture already covers adding a column to an existing table, and the live database is checked at deploy); the Complete Gas Day glossary entry is added.

---

## GCT-2 · Backfill the counts from the API for the days it still serves — [#647](https://github.com/mholubinka1/home-monitoring/issues/647)

**Blocked by**: #646

**User stories**: 5

### What to build

The existing summary backfill also records the half-hour counts for every day the API returns (2024-10-09 onward), leaving earlier days NULL. It requests everything the API serves, and it runs at startup whenever the summary's counted days reach back less than 6 months (replacing the one-time `job_run` gate), so a fresh, wiped or restored database fills itself and the first start after this deploy backfills the counts.

### Acceptance criteria

- [x] Given counted days reaching back less than 6 months (or none), when octopus-app starts, then the backfill runs; given 6 months or more, it is skipped.
- [x] Given API readings for a day, then the backfill stores that day's count alongside the total.
- [x] Given days before the API's first day, then their count stays NULL.
- [x] Given a partial API day, then the count is below the expected number and the day reads as incomplete.
- [x] Re-running the backfill leaves the counts unchanged.

---

## GCT-3 · Refresh the summary daily — [#648](https://github.com/mholubinka1/home-monitoring/issues/648)

**Blocked by**: None

**User stories**: 3

### What to build

Run the summary job daily, after the daily raw consumption refetch, instead of weekly (keeping the trailing 14-day window), so recent days settle as their gas arrives.

### Acceptance criteria

- [x] The summary job is scheduled daily and recorded in `job_run` like before.
- [x] Given a day whose gas arrives late, then the next daily refresh turns it from incomplete to complete with the right total.
- [x] The raw refetch runs before the summary in the same morning.
- [x] The existing weekly-cadence documentation is updated.

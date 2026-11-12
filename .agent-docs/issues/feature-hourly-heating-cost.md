# Issues: feature-hourly-heating-cost

## Estimated hourly heating cost

**Blocked by**: None

**User stories**: 1, 2, 7, 8

### What to build

The tracer through every layer: a new `heating_hour` table, a hand-mirrored read of hive-app's `heating_status`, and an hourly octopus-app job that writes one row per completed UTC hour with the minutes the heating was on, an estimated heating kWh (on-minutes times a 12 kW default), the gas unit rate in force at that hour and the cost. The job is registered on an hourly schedule with `job_run` recording. Measured gas, learning and backfill come in later slices.

### Acceptance criteria

- [x] Given 12 ON polls in a completed hour and no gas data, when the job runs, then one `heating_hour` row exists with 24 on-minutes, source `estimated`, heating kWh of `24/60 x 12`, and cost at the rate in force at that hour.
- [x] Given an hour whose polls are all `boost_active=1`, when the job runs, then those polls count as heating on.
- [x] Given an hour with fewer than 24 polls, when the job runs, then no row is written (and an existing row for it is removed).
- [x] Given the current, unfinished hour, when the job runs, then no row is written for it.
- [x] Given no gas rate in force, then the row is kept with a NULL rate and cost.
- [x] Given hive-app's `heating_status` table does not exist, when the job runs, then nothing is written and nothing raises.
- [x] Given the job is registered, then it runs every hour with retry-with-backoff and a recorded `job_run`.

---

## Measured gas and the hot-water baseline

**Blocked by**: #751

**User stories**: 3, 4, 5, 9

### What to build

Use metered half-hourly gas where it exists. A heating hour with both half-hours metered is `measured`, its heating kWh being the metered gas minus a hot-water baseline learned (median, per London hour of day, over the trailing 28 days) from measured hours with no heating on. Hours with no heating on but metered gas are stored as hot-water hours with 0 heating kWh. When gas arrives late, the next run turns an estimated hour into a measured one. Stored measured rows stay after raw gas is pruned at 45 days.

### Acceptance criteria

- [x] Given a heating hour with both half-hours metered at 4.5 kWh and a learned baseline of 0.3 kWh for its hour of day, when the job runs, then the row is `measured` with heating kWh 4.2 and the cost follows from it.
- [x] Given an hour with zero on-minutes and metered gas, then its row has 0 heating kWh and its metered kWh feeds that hour of day's baseline.
- [x] Given fewer than 5 zero-on hours for an hour of day, then the median over all hours of day is used; with none at all the baseline is 0.
- [x] Given a heating hour first written as estimated, when its gas arrives and the job runs again, then the row becomes `measured`.
- [x] Given a London day whose metered readings are all zero, then its hours are not taken as measured zero use.
- [x] Given a measured row older than the 45-day raw retention, when raw consumption is pruned and the job runs, then the row is unchanged.
- [x] Given a measured heating kWh would go below zero after the baseline, then it is 0, and it never exceeds the metered kWh.

---

## Learned effective boiler kW

**Blocked by**: #752

**User stories**: 6, 8

### What to build

Replace the fixed 12 kW with the effective boiler kW learned from the household's measured hours: over the trailing 28 days, the sum of heating kWh divided by the sum of on-hours, from measured hours with at least 20 on-minutes. Estimated hours use it. Below the minimum evidence it falls back to 12.

### Acceptance criteria

- [x] Given at least 5 measured hours with 20 or more on-minutes totalling 150 kWh over 14 on-hours, when an estimated hour with 24 on-minutes is written, then its heating kWh is `24/60 x 150/14`.
- [x] Given fewer than 5 such hours, then estimates use 12 kW.
- [x] Given measured hours older than 28 days, then they do not affect the learned kW.
- [x] Given a rate change part-way through the window, then each hour is priced at the rate in force at its own start.
- [x] Given a 25-hour clock-change day, then each UTC hour has exactly one row and the baseline is keyed by London hour of day.

---

## First-run backfill

**Blocked by**: #753

**User stories**: 1, 9

### What to build

When `heating_hour` is empty, the first run fills it chronologically from the earliest `heating_status` poll, one London day at a time, so each day's learned baseline and kW use only the days written before it. Days older than the retained gas window stay estimated. Later runs recompute only the last 4 London days.

### Acceptance criteria

- [x] Given an empty `heating_hour` table and polls over several days, when the job runs, then rows exist for every complete hour from the earliest poll.
- [x] Given that backfill, then each day's baseline and kW come only from earlier days' rows.
- [x] Given polls older than the retained gas window, then those hours are `estimated`.
- [x] Given a populated table, when the job runs, then rows older than the last 4 London days are not recomputed.
- [x] Given the job stopped for more than 4 days, when it runs again, then the missed days are backfilled from the hour after its newest stored hour, including the rest of a day it stopped part-way through.

---

## Dashboard panel, removals and docs

**Blocked by**: #752

**User stories**: 1, 4, 10, 11

### What to build

Replace panel 3 with "Heating Cost per Hour": stacked bars in pounds with a solid measured series and a lighter estimated series, read from `heating_hour`, over the dashboard's existing 14 days back. Delete the `boiler_kw` variable and the boost-minutes and boost-cost queries from the dashboard JSON. Update the queries document (schema block, variables, panel section), the plain-language explainer, and the dashboard entry in the glossary. Deploy order is the app first, then the dashboard import.

### Acceptance criteria

- [x] Given the dashboard JSON, then panel 3 is "Heating Cost per Hour" with a measured and an estimated series, and no `boiler_kw` variable or boost-minutes query remains (checked by the dashboard JSON test).
- [x] Given the queries document, then it describes `heating_hour`, the new panel's query, and records that its check against a database is pending, and no longer mentions `boiler_kw` or the Boost Minutes panel.
- [x] Given `docs/heating-model.md`, then it explains hourly heating cost, the hot-water split and the learned kW in plain language.
- [x] Given `.agent-docs/context.md`, then its gas dashboard entry names the new panel 3.
- [ ] Given the panel query, when it is run read-only on a throwaway copy of the database, then it returns the expected measured and estimated hourly rows without exceeding the Pi's memory limit.

---

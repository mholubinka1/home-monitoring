# Remove the backend that only fed the removed gas dashboard panels

## Problem Statement

The gas/heating dashboard was cut from 13 panels to 6 (PR #713). The octopus-app still computes and stores everything the removed panels showed: a verdict for every day, weekly efficiency rows, a bootstrapped upper threshold, a noise model, and a thermostat cross-check that labels each fit "estimated" or "confirmed". None of it is read by the six remaining panels or by the cost forecast. It costs a weekly refit's worth of computation, 2 tables, 8 model columns and about 1,000 lines of code and tests that the owner has to maintain and no longer understands.

## Solution

Remove all of it. The Heating Model job keeps doing the one thing the dashboard and the cost forecast use: fit the model weekly and write the 7-day gas forecast from the newest usable fit. The docs describe the system as it is: a fit and a forecast, no verdicts or weeks.

## User Stories

1. As the owner, I want the heating job to fit the model and write the forecast without computing day verdicts, weeks, a bootstrap, a noise model or a thermostat label, so that the job does only what the dashboard uses.
2. As the owner, I want the Daily Gas forecast line and the cost forecast to be unchanged, so that removing code changes no number on the dashboard.
3. As the owner, I want the unused tables and columns dropped with a documented, order-safe sequence, so that I can run it on the Pi without a failed weekly refit or an outage.
4. As the owner, I want ADR-0030, the heating-model explainer and the glossary to describe only what remains, so that the docs do not describe features that are gone.
5. As a maintainer, I want code left without a caller after this removal (readers, types, constants, imports, test helpers) removed too, so that no dead code is left behind.

## Implementation Decisions

- **Removed modules (octopus-app data):** verdicts, weeks, bootstrap, thermostat cross-check.
- **Heating job:** `refresh` still reads the usable gas days and daily mean temperatures, refits when the latest fit is 7 or more local days old or no usable fit exists, records an unusable fit, and rewrites the forecast from the newest usable model. The verdict, week, bootstrap, noise and thermostat steps go. A usable fit record keeps threshold, slope, weight, r_squared, days_used, baseload and the three baseload day counts; the forecast needs threshold, slope, weight and baseload. When no baseload can be measured, the forecast table is still emptied and nothing is judged (unchanged).
- **MariaDB client:** the replace and read methods for day verdicts and weeks go, and so does `read_heating_readings`, which only the thermostat check used (with its `HeatingReading` type, the read chunk constant and the cross-app `heating_status` table reference, if nothing else uses them).
- **Models:** `heating_day_verdict` and `heating_week` are removed from the model. `heating_model` loses `upper_threshold`, `noise_base`, `noise_per_degree_day`, `noise_floor`, `thermostat_threshold`, `heating_days`, `non_heating_days` and `label`. The matching `HeatingModelRecord` fields and the verdict and week records go.
- **Schema (manual, one-off, never run by the app):** Schema Sync is additive only (ADR-0005), and the Pi's watchtower may replace the image before anyone runs SQL. So the migration is two phases. Phase 1, before the new image runs: `ALTER TABLE heating_model MODIFY COLUMN IF EXISTS label VARCHAR(20) NULL;` (old code still writes it, new code omits it; both work; `IF EXISTS` makes it safe to re-run). Phase 2, after the new image is running: drop the 2 tables and the 8 columns. The SQL and this order are documented in the README next to the existing manual-step guidance.
- **Docs:** ADR-0030 gets a dated amendment saying the verdict, week, bootstrap, noise and thermostat parts are superseded, with the reason. `docs/heating-model.md` sections 7-10b and 12 and the glossary are rewritten to match; Heating Verdict, Heating Week and related glossary entries are removed. The research and spec files from the original build stay as history.
- **Orphan hunt:** after the removal, find every function, type, constant, import, fixture and config value in octopus-app with no remaining caller and remove it.
- **Untouched:** `heating_model.py` (effective temperature, baseload, away days, fit), `heating_forecast.py`, `heating_forecast_day`, the Grafana dashboard, hive-app.

## Testing Decisions

- A good test drives the public seam and checks stored results, not internals. The seam stays the Heating Model job's `refresh` against the real `mariadb_client` fixture, plus the existing schema-sync test.
- Tests of removed behaviour are deleted with it (verdict, weeks, bootstrap, thermostat, their persistence, the cross-app readings test, the verdict and week assertions inside the job tests). Job tests keep their fit, refit-cadence, unusable-fit, forecast and empty-table scenarios, with the removed fields dropped.
- Test that a usable fit and an unusable fit still store a model row, and that the forecast from a stored model is unchanged.
- Test that Schema Sync no longer lists the removed tables, and that a database that still has the old tables and columns keeps working (the old `label` made nullable) with the new code.
- Prior art: the existing heating job and model persistence tests.

## Out of Scope

- Any change to the dashboard JSON, the cost forecast, hive-app, or the fit maths.
- New models (comfort, thermal, flow temperature) and the other workstreams (B to G).
- Running the SQL on the Pi (the owner does that).

## Further Notes

- Workstream A2 of the gas/heating rescope. The Grafana dashboard import follows this change.

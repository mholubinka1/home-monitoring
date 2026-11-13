> Work complete — PR ready to merge.

# Issues: feature-thermal-model-backtest

## Thermal model fitted and stored

**GitHub issue**: #767

**Blocked by**: None

**User stories**: 1, 2, 5, 6, 7

### What to build

The tracer through every layer: a new `thermal_model` table, a read of the hourly outdoor temperature and radiation from hive-app's `weather_observation`, pure fitting functions, and an octopus-app job that fits the five-number thermal model by least squares over the hourly steps of the last 60 days and stores one row per fit, usable or not, with the reason when not. An hour is a step only when its readings are complete. A run does nothing when a fit was already made today. The job is not scheduled yet and there is no backtest yet; both come in later slices. Part of workstream E (ADR-0035).

### Acceptance criteria

- [x] Given 10 days of history generated from known model numbers, when the job runs, then a `thermal_model` row is stored with those numbers.
- [x] Given the same history with some hours missing their first-10-minutes reading, their heating hour or their weather, when the job runs, then those hours are left out and the numbers are still recovered.
- [x] Given under a week of hourly steps, when the job runs, then the row is unusable with reason `too_few_steps`.
- [x] Given a history with almost no heating, when the job runs, then the row is unusable with reason `too_little_heating`.
- [x] Given a history whose inputs do not vary, when the job runs, then the row is unusable with reason `no_fit`.
- [x] Given a history in which heating cools the room, when the job runs, then the row is unusable with reason `implausible`.
- [x] Given a fit stored earlier today, when the job runs again, then nothing is written; and given the newest fit was made yesterday, when the job runs, then a new row is written.
- [x] Given no thermostat readings, or hive-app's `heating_status` or `weather_observation` table missing, when the job runs, then nothing is written and nothing raises.

---

## Backtest and the 0.5 C gate

**GitHub issue**: #768

**Blocked by**: #767

**User stories**: 3, 4

### What to build

Every fit is backtested before it is stored: each of the 14 local days before the fit is replayed hour by hour from its first reading by a model fitted only on the 60 days before that day, with the weather and heating that really happened. A day is replayed only when every one of its hours is a step and the earlier model passes the data and plausibility checks. The row stores the number of days replayed and the mean and worst absolute error, and is unusable when fewer than 5 days could be replayed or the mean error is above 0.5 C.

### Acceptance criteria

- [x] Given a history that follows the model exactly, when the job runs, then the row records 14 backtest days with a mean and worst error near zero and is usable.
- [x] Given one backtest day with a gap in its hours, when the job runs, then that day is skipped and 13 backtest days are recorded.
- [x] Given fewer than 5 backtestable days, when the job runs, then the row is unusable with reason `too_few_backtest_days`.
- [x] Given a last fortnight that behaves differently from what went before, when the job runs, then the mean error is above 0.5 C and the row is unusable with reason `backtest_error_too_high`.
- [x] Given two histories that differ only in the readings of the newest backtest day, when the job runs on each, then the stored backtest errors differ, showing the day was replayed by a model that had not seen it.

---

## Hourly chain and the guide

**GitHub issue**: #769

**Blocked by**: #767

**User stories**: 8, 9

### What to build

The thermal model job joins the hourly job chain: after a successful heating-hour run the comfort days are refreshed and then the thermal model, each with its own job record (`thermal_model_refresh`); after a failed heating-hour run both are recorded as skipped. The thermal step does not depend on the comfort step. The heating guide gains a plain-language section on the thermal model and its backtest, and the glossary's database entry lists the new table.

### Acceptance criteria

- [x] Given the hourly heating-hour run succeeded, when the chain runs, then the comfort days and then the thermal model are refreshed, each recorded as its own `job_run`.
- [x] Given the heating-hour run failed, when the chain runs, then the thermal model refresh is recorded as skipped.
- [x] Given a failing comfort-day refresh, when the chain runs, then the thermal model refresh still runs.
- [x] Given the heating guide, when a reader looks for the thermal model, then a plain-language section explains the five numbers, the backtest and the 0.5 C gate, and the glossary's database entry lists `thermal_model`.

---

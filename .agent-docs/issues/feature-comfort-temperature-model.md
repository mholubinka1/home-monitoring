# Issues: feature-comfort-temperature-model

## Comfort temperature learned from boost starts

**GitHub issue**: #757

**Blocked by**: None

**User stories**: 1, 2, 4, 5, 8

### What to build

The tracer through every layer: a new `comfort_day` table, reads of boost polls from hive-app's `heating_status`, and an octopus-app job that writes today's row with the comfort temperature (the median room temperature at the boost starts of the 90 days before the day) and the number of boost starts used. The job runs in the hourly chain straight after the heating-hour job, with its own `job_run` record. Counted hours, minutes below comfort, carry-forward and history come in later slices.

### Acceptance criteria

- [ ] Given five boosts on earlier days that started with the room at 16.0, 16.5, 17.0, 17.5 and 20.0 C, when the job runs, then today's `comfort_day` row has comfort temperature 17.0 and 5 boost starts.
- [ ] Given a boost earlier today, when the job runs, then it is not evidence for today's comfort temperature.
- [ ] Given boost polls 2 minutes apart, then they are one boost start whose temperature is the first poll's, and given a boost poll more than 10 minutes after the previous one, then it starts a new boost.
- [ ] Given a boost more than 90 days before today, when the job runs, then it is not used.
- [ ] Given fewer than 5 boosts in the window and no earlier comfort temperature stored, when the job runs, then today's row has no comfort temperature.
- [ ] Given hive-app's `heating_status` table does not exist, when the job runs, then nothing is written and nothing raises.
- [ ] Given the hourly heating-hour run succeeded, when the hourly chain runs, then the comfort job runs after it and records its own `job_run`; given the heating-hour run failed, then the comfort job is recorded as skipped.

---

## Frozen days, carry-forward and first-run history

**GitHub issue**: #758

**Blocked by**: #757

**User stories**: 3, 7

### What to build

Each run computes the London days from the newest stored day to today, oldest first, so a finished day is settled on the first run after it ends and then never rewritten. On an empty table the run fills every day from the first thermostat poll, each from its own earlier evidence. When a day's window holds fewer than 5 boosts, the newest earlier stored comfort temperature carries on. A day with no polls gets no row.

### Acceptance criteria

- [ ] Given a stored row for today, when the job runs again after another boost-free hour, then today's row is rewritten (its `computed_at` moves).
- [ ] Given yesterday's row was last written yesterday, when the first run after London midnight happens, then yesterday's row is settled and today's row is written.
- [ ] Given a settled row for a day before yesterday, when new boost evidence for its window appears and the job runs, then that row is unchanged.
- [ ] Given an empty table and polls reaching back 10 days, when the job runs, then every one of those days has a row, each learned only from boosts before that day.
- [ ] Given a window with fewer than 5 boosts and an earlier day's stored comfort temperature of 17.0, when the job runs, then the day's comfort temperature is 17.0.
- [ ] Given a day with no thermostat polls, when the job runs across it, then that day has no row.

---

## Counted hours and minutes below comfort

**GitHub issue**: #759

**Blocked by**: #757

**User stories**: 6, 11

### What to build

Each `comfort_day` row also stores the day's counted hours (the London clock hours with heating-on minutes in the `heating_hour` rows of the 90 days before the day) and the minutes the room was strictly below the comfort temperature during those hours (2 minutes per poll).

### Acceptance criteria

- [ ] Given a comfort temperature of 17.0, heating in the 18:00 London hour within the last 90 days, and today 10 polls at 16.5 C in the 18:00 hour, when the job runs, then today's row has counted hours `18` and 20 minutes below comfort.
- [ ] Given polls below comfort in an hour that is not a counted hour, then they add no minutes.
- [ ] Given polls at exactly the comfort temperature, or with no room temperature, then they add no minutes.
- [ ] Given a clock hour heated only more than 90 days ago, then it is not a counted hour.
- [ ] Given heating at 17:00 UTC on a British Summer Time day, then the counted hour is 18 (London time).
- [ ] Given a day with no comfort temperature, then its minutes below comfort are NULL, not 0.

---

## Dashboard: comfort line and minutes below comfort

**GitHub issue**: #760

**Blocked by**: #758, #759

**User stories**: 9, 10, 12

### What to build

Panel 1 gains a stepped "comfort" line read from `comfort_day`. Panel 4 becomes "Minutes Below Comfort", bars read from `comfort_day`. The `comfort_target` and `comfort_margin` variables and the old query are deleted. The queries document and the plain-language explainer are updated. The panel queries are run on real data only in the final dashboard review.

### Acceptance criteria

- [ ] Given the gas heating dashboard JSON, when panel 1 is read, then it has a query returning `time` and `comfort` from `comfort_day`, drawn as a step in its own colour.
- [ ] Given the comfort query, then the newest day's value is repeated at the end of that day so the line reaches the present.
- [ ] Given the gas heating dashboard JSON, when panel 4 is read, then it is titled "Minutes Below Comfort" and reads `minutes_below` from `comfort_day` with no read of `heating_status`.
- [ ] Given the dashboard variables, then `comfort_target` and `comfort_margin` are gone and every remaining variable is still used.
- [ ] Given the queries document, then it lists `comfort_day`, both new queries, and no longer the two variables or the old comfort query.
- [ ] Given the explainer `docs/heating-model.md`, then it has a plain-language section on the comfort temperature.

---

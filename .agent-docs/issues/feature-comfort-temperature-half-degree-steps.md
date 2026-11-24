# Issues: feature-comfort-temperature-half-degree-steps

## Prefactor: the comfort job gets the Comfort Windows and full thermostat polls

**GitHub issue**: #831

**Blocked by**: None

**User stories**: 17 (enabling)

### What to build

No behaviour change. `ComfortDayJob` takes the Comfort Windows, and `main` passes the configured ones, as it already does for the room forecast job. The job's per-day read of thermostat polls is replaced by one that also returns Hive's target, the heating state and whether a boost was active. The read starts a boost gap before the day, so later slices can find Held Boosts and Quiet Days from one read.

### Acceptance criteria

- [x] Given the existing comfort-day scenarios, when `ComfortDayJob(mariadb, comfort_windows)` runs, then the stored rows are the same as before.
- [x] Given octopus-app starts, when the hourly chain is registered, then the comfort job is built with the configured Comfort Windows.

---

## A Held Boost raises the Comfort Temperature and the plan holds it

**GitHub issue**: #832

**Blocked by**: #831

**User stories**: 2, 3, 4, 5, 8, 10, 11, 12, 14, 15, 16, 17

### What to build

Replace the boost-median rule with the staircase's upward half and its seed. `comfort_day` gains `raised_at`, `day_kind` and `quiet_count` (Schema Sync, additive). Each day starts from the previous stored row (a pre-change row rounded up to a half degree; none at all means the 16 °C floor). The first Held Boost of a day sets `raised_at`, and from then on the value in force is +0.5. Minutes below compare each poll with the value in force, and `boost_starts` becomes the day's own count. The room forecast job plans for the value in force (stepped values are not rounded). The median code, its constants and the 730-day boost read are removed.

### Acceptance criteria

- [x] Given a pre-change comfort_day row of 16.8 yesterday, when today is written, then today starts at 17.0 with a quiet count of 0.
- [x] Given no earlier comfort_day row, when the first day is written, then it starts at 16.0.
- [x] Given today holds 17.0 and a boost starts in the Comfort Window at a room of 16.7 with the poll before it showing a Hive target of 17.0, when the job runs, then today has `raised_at` at that boost, `day_kind` `held_boost`, quiet count 0, and the next day starts at 17.5.
- [x] Given a second Held Boost later the same day, when the job runs, then today is raised only once.
- [x] Given a boost at a room of 16.4 against a target of 17.0, when the job runs, then it is not held and the quiet count is unchanged.
- [x] Given a boost outside the Comfort Window, when the job runs, then it is not held.
- [x] Given a boost whose previous poll showed a Hive target other than the Comfort Temperature (dry run, Away Override, manual change), when the job runs, then it is not held.
- [x] Given a Comfort Temperature of 20.0 and a Held Boost, when the next day is written, then it starts at 20.5.
- [x] Given a raise at 19:00, when minutes below are counted, then readings before 19:00 compare with 17.0 and readings after with 17.5.
- [x] Given 3 Boost Starts on the day, when the job runs, then `boost_starts` is 3.
- [x] Given today's row is 17.0 with `raised_at` set, when the room forecast runs, then the plan's slots hold 17.5; given 17.5 with no raise they hold 17.5; given a pre-change row of 16.8 they hold 17.0.
- [x] Given yesterday was settled under the new rule, when its evidence changes later, then it is not rewritten.
- [x] Given no `heating_status` table, when the job runs, then nothing is written and nothing raises.

---

## 3 Quiet Days step the Comfort Temperature down, never below 16

**GitHub issue**: #833

**Blocked by**: #832

**User stories**: 1, 6, 7, 8, 9, 15

### What to build

The staircase's downward half. A day with no Held Boost is a Quiet Day when, inside the Comfort Window, held polls outside No One Is Home add up to at least 2 hours and the heating was on at least once. Its quiet count goes up by one; a day that is neither leaves the count unchanged. When a settled day's count reaches 3, the next day starts 0.5 lower, never below the hardcoded 16 °C floor, and the count resets.

### Acceptance criteria

- [ ] Given 3 days in a row, each with at least 2 hours in the window held at home, the heating on and no Held Boost, when the job runs, then each is `quiet` with counts 1, 2, 3 and the next day starts 0.5 lower with count 0.
- [ ] Given a day with only 1 hour held at home in the window, when the job runs, then it is neither quiet nor a raise and the count is unchanged.
- [ ] Given held polls during a No One Is Home period, when the job runs, then they do not count towards the 2 hours.
- [ ] Given a day with the heating never on in the window, when the job runs, then it is not quiet.
- [ ] Given counts 1, 2, then a day away, then a Quiet Day, when the job runs, then the step happens after the fourth day.
- [ ] Given a Comfort Temperature of 16.0 and 3 Quiet Days, when the next day is written, then it stays 16.0 with count 0.

---

## The comfort line shows the steps and the docs follow

**GitHub issue**: #834

**Blocked by**: #833

**User stories**: 13, 18

### What to build

The gas dashboard's comfort line (panel 1, query D) adds a point at `raised_at` with the raised value, so a raise part-way through a day shows as a step, and its closing point carries the value in force at the end of the day. Panel descriptions, the queries document, the heating-model explainer and the old comfort spec are updated for the stepped Comfort Temperature (ADR-0040).

### Acceptance criteria

- [ ] Given a day with `raised_at`, when the dashboard's comfort query is read, then it has a point at midnight with the start value and a point at `raised_at` with +0.5, drawn as steps.
- [ ] Given the queries document, when compared with the dashboard, then the comfort query and panel text match and link ADR-0040.
- [ ] Given the explainer, when read, then section 11 describes the steps with a worked example and "rounded up to a half degree" is gone from the plan text.

---

# Weekly schedule is stored without the library's added keys (E1 fix)

## Problem Statement

hive-app's `heating_schedule` table (E1, PR #766) is meant to hold Hive's **Weekly Schedule** as Hive returns it, one row per change. It probably never receives a row, or receives one on every poll. Both come from one cause: the schedule is read from the Hive library's live product data after the library has changed it.

In the Hive API source's heating poll, `getScheduleNowNextLater` runs before the weekly schedule is read. In apyhiveapi 1.0.9, `getScheduleNowNextLater` passes the product's `state.schedule` to `HiveHelper.getScheduleNNL` with no copy, and `getScheduleNNL` writes `Start_DateTime` into every slot dict (and `End_DateTime` into the now, next and later slots) in place. This happens only when the thermostat is online and in SCHEDULE mode. The weekly schedule is then read from that same dict.

Checked against the installed library: nothing else in the library touches the heating schedule. `startSession` and `createDevices` only store the JSON Hive sent, so the data is as Hive returned it until `getScheduleNowNextLater` runs.

Expected effects, read from the code and not yet seen on a live poll:

- the datetimes cannot be stored in the JSON column, so `write_weekly_schedule` fails and the failure is logged as a warning and swallowed;
- if they were stored, `Start_DateTime` moves forward on each poll, so every poll would compare as a changed schedule and add a row.

E1's tests used a fake that does not mutate, and that shares one product dict across polls, so they passed. E1 is not yet deployed. `heating_schedule` does not exist on the Pi, so there is no real payload to check against.

## Solution

The Hive API source takes the weekly schedule as plain Hive data: a deep copy of the library's schedule, made before any heating getter runs, holding only the keys Hive sent. The stored row and the change comparison then depend only on what Hive programmed.

## User Stories

1. As the household, I want the weekly schedule stored as Hive returns it, so that the room forecast (E3) reads real slots.
2. As a maintainer, I want a new `heating_schedule` row only when the programmed schedule changes, so that the poll's own bookkeeping never counts as a change.
3. As a maintainer, I want the stored schedule to be the same in SCHEDULE mode and every other mode, so that switching mode does not look like an edit.
4. As a maintainer, I want a test whose fake library mutates the schedule as the real one does, so that this cannot return unseen.

## Implementation Decisions

- **Hive API source (`HiveApiSource`)**: in the heating poll, read the weekly schedule straight after the climate device is found and before any heating getter. Placing it first, not just before `getScheduleNowNextLater`, means reordering the getters cannot reopen the bug.
- The weekly-schedule helper returns a `copy.deepcopy` of the schedule. The existing rule stays: a missing, empty or non-dict schedule gives None, logged at DEBUG.
- Do not strip keys by name. The copy is the fix; stripping `Start_DateTime` and `End_DateTime` would leave the next library key to break it.
- Do not pass the weekly schedule through `_json_safe` (the earlier fix for the now, next and later column, `bugfix-hive-heating-schedule-json-datetime`). It would make the write succeed, but `Start_DateTime` moves forward every poll, so every poll would still store a new row.
- A one-line comment at the copy records the cause: the library adds datetimes to the slots.
- `persist_heating_status` and the MariaDB client's `write_weekly_schedule` are unchanged. The existing comparison of the parsed JSON stays.
- No schema change, no change to the now, next and later `schedule` column, and no change to how that column is serialised (`_json_safe`). The library still mutates its own data, which that column is built from.
- Performance: one deep copy of a few dozen small dicts every 120 seconds; negligible.
- No ADR: the decision is easy to reverse.

## Testing Decisions

Seam: `HiveApiSource.fetch_heating_status()` with the test fake for apyhiveapi's `Hive` (`FakeApyHive`), then `persist_heating_status` to the SQLite fixture. Tests assert stored rows and returned values, not internals. Prior art: the E1 weekly-schedule tests in hive-app (`test_heating_weekly_schedule.py`, where the new scenarios go) and `test_heating_working_flag.py`.

The fake changes to behave like the library:

- **Fresh data per poll.** The fake keeps the product state it was given and rebuilds its product data from a deep copy of it in `startSession`, as the real `getDevices` loads fresh JSON. Without this, the shared fake would carry one poll's mutations into the next, and even a correct fix would fail.
- **Real mutation.** In SCHEDULE mode only, the fake's `getScheduleNowNextLater` calls apyhiveapi's own `HiveHelper().getScheduleNNL` on the product schedule, catching `KeyError` as the library does. The mutation is the library's own, so the test follows library upgrades that add keys.
- **Configurable mode.** The fake's heating mode can be set; the default stays SCHEDULE.

Scenarios, each also a red-green step:

- a poll in SCHEDULE mode stores a weekly schedule with no `Start_DateTime` or `End_DateTime` in any slot, and the write does not fail;
- two polls with an unchanged programmed schedule add one row in total;
- an edited slot adds a second row and keeps the first;
- the weekly schedule of a poll is equal in SCHEDULE mode and in another mode, for the same programmed schedule;
- a poll with a missing, empty or non-dict schedule is still stored and adds no schedule row (existing behaviour). Missing and empty run in SCHEDULE mode. Non-dict runs in MANUAL mode: in SCHEDULE mode the library itself raises `TypeError` on a list schedule before hive-app's rule applies, and the now, next and later call is out of scope.

The existing test that a weekly schedule which cannot be written is logged and does not fail the poll also moves to MANUAL mode. Its schedule (`{"monday": {390, 435}}`, a set) is parsed by the real helper only when today is Monday, where the library raises `TypeError`; on other days the library hits `KeyError` first. The test checks the logged write failure, not parsing, so MANUAL mode keeps it the same every day.

## Out of Scope

- E3 (room forecast) and E2 (thermal model); no octopus-app change.
- Deploying hive-app, or any Pi database change. The household deploys.
- Judging the real payload: the first stored row after deploy is the check, and E3's parser stays defensive.
- Changes to the poll interval, the retry behaviour or the now, next and later slots, including guarding the library's `TypeError` on a malformed schedule in SCHEDULE mode.
- Hot water schedules (hive-app does not poll hot water).

## Further Notes

- The glossary tidy that `init-agent-docs` made at the start of this work is committed separately on this branch.
- After deploy, the first `heating_schedule` row is the real-world check: it should hold day names with slot lists of `start` and `value`, and nothing the library adds.

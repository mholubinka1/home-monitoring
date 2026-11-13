# Issues: bugfix/weekly-schedule-before-library-mutates

## Prefactor: the Hive test fake gives each poll fresh product data

**GitHub issue**: #771

**Blocked by**: None

**User stories**: 4

### What to build

Make the hive-app test fake for apyhiveapi's `Hive` behave like a new `Hive` on each poll, as production does: the fake keeps the product state it was given and reloads its product data from a deep copy of it whenever a session starts (as the real `getDevices` loads fresh JSON). Its heating mode can also be set, defaulting to SCHEDULE. No production change; this is what lets slice 2's mutating fake give honest results across several polls.

### Acceptance criteria

- [x] Given a fake whose product schedule is changed by one poll, when a second poll runs, then the second poll sees the schedule as first given.
- [x] Given the existing hive-app test suite, when it runs against the reworked fake, then every test still passes.

---

## Weekly schedule is taken before the library mutates it

**GitHub issue**: #772

**Blocked by**: #771

**User stories**: 1, 2, 3, 4

### What to build

The Hive API source reads the **Weekly Schedule** straight after finding the climate device, before any heating getter, as a deep copy of the library's product schedule, with a one-line comment naming the cause. The test fake's now/next/later call runs apyhiveapi's own `getScheduleNNL` in SCHEDULE mode only, catching `KeyError` as the library does, so the tests see the real in-place mutation. Persistence, schema, the now/next/later column and `_json_safe` are unchanged.

### Acceptance criteria

- [x] Given a thermostat in SCHEDULE mode with a programmed weekly schedule, when a poll is fetched and persisted, then the stored weekly schedule has no `Start_DateTime` or `End_DateTime` in any slot and no "weekly schedule not stored" warning is logged.
- [x] Given an unchanged programmed schedule in SCHEDULE mode, when two polls are fetched and persisted, then exactly one `heating_schedule` row is stored.
- [x] Given a stored weekly schedule, when a poll in SCHEDULE mode returns the schedule with one slot edited, then a second row is stored and the first is kept.
- [x] Given the same programmed schedule, when one poll runs in SCHEDULE mode and another in MANUAL mode, then both polls return equal weekly schedules.
- [x] Given a missing or empty schedule in SCHEDULE mode, or a non-dict schedule in MANUAL mode, when a poll is fetched and persisted, then the poll is stored and no `heating_schedule` row is added.

---

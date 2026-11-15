# Issues: feature/schedule-table-panel

## Current-schedule slots in a new table panel

**GitHub issue**: #787

**Blocked by**: None

**User stories**: 1, 3, 7, 8, 9, 10

### What to build

Store the day's Hive schedule slots and show them in a new table panel. The hourly room-forecast job writes a new small table of slots (local day, scenario, order, start, end, target) for today and tomorrow, replacing the current-schedule rows each run. The gas and heating dashboard gains its one table panel, with a "Current schedule" column that lists today's slots as `hh:mm - hh:mm | N degrees` and changes day at 00:00 London time. The dashboard test is reworded to allow this one table.

### Acceptance criteria

- [x] Given a made-up schedule with slots 05:40-06:25 at 19 and 06:25 on at 7, when the job runs, then today's current slots are 05:40-06:25 at 19 and 06:25-00:00 at 7.
- [x] Given a day whose first slot starts after midnight, when the job runs, then the previous day's last target runs from 00:00 to that slot.
- [x] Given two consecutive slots with the same target, when the job runs, then they are stored as one slot.
- [x] Given an unreadable weekly schedule, when the job runs, then no current slots are stored and earlier ones are removed.
- [x] Given Hive's mode is OFF, when the job runs, then the slots are still stored.
- [x] Given a DST changeover day, when the job runs, then the slots' stored times are the correct instants.
- [x] Given the dashboard file, when it is checked, then it has exactly one table panel and it reads room_forecast_slot for the local day.

---

## Comfort plan slot

**GitHub issue**: #788

**Blocked by**: #787

**User stories**: 2, 5, 6, 7

### What to build

Expose the comfort plan's held span (pre-heat start to window close) from the plan calculation, store it as the plan's one slot per day at the Comfort Temperature rounded up to a half degree, keep the start once it has passed, and add the "Comfort plan" column to the table.

### Acceptance criteria

- [x] Given a comfort temperature and a window closing at midnight, when the job runs, then the plan slot runs from the pre-heat start to 00:00 at the temperature rounded up to a half degree.
- [x] Given a stored plan start already passed, when a later run recomputes, then the start is kept and the end and target update.
- [x] Given a stored plan start still ahead, when a later run recomputes, then the new start replaces it.
- [x] Given no comfort temperature, when the job runs, then no plan slots are stored and earlier ones are removed.

---

## Estimated cost to midnight row

**GitHub issue**: #789

**Blocked by**: #787, #788

**User stories**: 4

### What to build

Add a last row to each column of the table: the estimated gas cost from now to local midnight under that scenario, summed from the live room-forecast hour rows. Update the heating guide to describe the table, the kept plan start and the cost to midnight.

### Acceptance criteria

- [ ] Given live rows for the rest of today, when the panel's query runs, then each column's last row is the sum of its cost to local midnight.
- [ ] Given no slots in a column, when the panel's query runs, then that column's cost is empty.
- [ ] Given the heating guide, when the slice is done, then it describes the table, the kept start and the cost to midnight.

---

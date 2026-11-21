> Work complete — PR ready to merge.

# Issues: bugfix-comfort-schedule-allow-no-midnight-entry

## Comfort Schedule writes days with no 00:00 entry

**GitHub issue**: #823

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5

### What to build

Drop hive-app's own rule that each day's first entry is at 00:00, so a week taken from
Hive (whose days have no 00:00 entry) is written instead of refused. Build today and
tomorrow the way Hive writes its own days: the slot's target from its start and the Away
Temperature from its end, with no Away entry invented at 00:00. A slot ending at midnight
closes with the Away Temperature at 23:59, so its target never runs on into the next day.
The feature spec and README follow.

### Acceptance criteria

- [x] Given Hive's week has no 00:00 entry on any day, when the heating poll runs with control `on`, then the week is written and the five untouched days are as Hive holds them.
- [x] Given a 05:40–06:25 plan slot at 19 °C, when the week is built, then that day is exactly 19 °C from 05:40 and Away from 06:25, with no entry at 00:00.
- [x] Given a plan slot ending at midnight, when the week is built, then that day closes with the Away Temperature at 23:59.
- [x] Given a plan slot starting at midnight, when the week is built, then it is the day's only entry at minute 0.
- [x] Given a week whose days have no 00:00 entry, when HiveSource writes it, then it is sent and verified, and the other write rules still refuse a malformed week.

---

# Comfort Schedule: allow days with no 00:00 entry

## Problem Statement

With `comfort_schedule.control: on`, every Comfort Schedule write is refused with
`ValueError: monday does not start at midnight`, so the Comfort Plan never reaches Hive.
Hive's own weekly schedule has no 00:00 entry on any day: a day's last setting carries
on until the next day's first entry. The five weekdays the writer leaves alone come
straight from Hive's week, so hive-app's own rule "the first entry of each day is at
00:00" (a check neither Hive nor the 2.0 beta enforces) refuses every week Hive itself
holds.

Dropping that rule alone isn't enough. The writer starts each day it builds with an
Away entry at 00:00, so a slot ending at midnight has no closing entry, and the next
day's 00:00 entry ends it. If the next day keeps Hive's entries (no 00:00 entry), the
slot's target runs on through the night until that day's first entry. The default
weekend window ends at 00:00, so Sunday's Comfort Temperature already runs on into
Monday morning.

## Solution

A day with no 00:00 entry is valid. The writer builds today and tomorrow the way Hive
writes its own days: the slot's target from its start and the Away Temperature from its
end, with no leading Away entry at 00:00. Before a day's first entry, Hive holds the
previous day's last setting. A slot that ends at midnight is closed by an Away entry at
23:59 (minute 1439), so no written day hands its Comfort Temperature on to the next
one. The Comfort Window loses only its final minute.

## User Stories

1. As the householder running `comfort_schedule: on`, I want a week whose days have no
   00:00 entry to be written, so that the Comfort Plan reaches Hive with Hive's own
   week as the base.
2. As the householder, I want the days the Comfort Schedule writes to have no invented
   Away entry at 00:00, so that they take the same form as the days Hive writes.
3. As the householder, I want a plan slot that ends at midnight to close with the Away
   Temperature at 23:59, so that heating doesn't run at the Comfort Temperature through
   the night into the next day.
4. As the householder, I want a plan slot that starts at midnight to be just the day's
   first entry, so that it never collides with another entry at minute 0.
5. As the householder, I want every other write rule (seven weekdays, non-empty days,
   unique whole-minute starts from 0 to 1439, targets in half degrees) still enforced,
   so that a malformed week is still refused before anything is sent.

## Implementation Decisions

- **Comfort schedule module (`hive_app.data.comfort_schedule`).**
  - `validate_week` drops the "does not start at midnight" rule. Every other rule and
    its message is unchanged, and the docstring no longer mentions midnight.
  - `build_week` no longer inserts an Away entry at minute 0. Each slot gives its
    target at its rounded-down start and the Away Temperature at its rounded-up end.
    An end at the next local midnight (minute 1440) gives the Away Temperature at
    minute 1439 instead of no closing entry. A slot starting at minute 0 is simply the
    day's first entry, so the special case for it goes. A slot whose start rounds to
    1439 and which ends at midnight would collide with its own closing entry. Plan
    slots start on 2-minute marks, so a start of 1439 can't happen, and if it did
    `validate_week` would refuse the duplicate start.
  - "A slot outside its day" (`_lies_within`) and the no-plan-slot case are unchanged.
- **Heating module (`hive_app.data.heating`).** No change. `ComfortScheduleControl`
  already calls `validate_week` before an `on` write or a dry-run record, and logs and
  records a refusal by its message.
- **No schema, config or octopus-app change.** The dry-run fingerprint is taken of the
  built week, so a dry run records the new shape once, as for any change in the week.
- **Feature spec.** `feature-presence-autonomous-heating` ("Write path", "Building the
  week" and "A slot starting at midnight") is amended to match, and README's Comfort
  Schedule section follows.

## Testing Decisions

- Test external behaviour through the two seams the #808 tests already use: no new
  seam, and no unit test of `build_week` or `validate_week` on their own.
- **`HeatingRetriever.refresh(now)`** with the fake HiveSource and the real MariaDB
  test client (`test_comfort_schedule_write.py`):
  - given Hive's week has no 00:00 entry on any day, the poll writes the week, and the
    other five days are as Hive holds them;
  - a built day has no leading Away entry: the made-up 05:40–06:25 slot at 19 °C gives
    exactly `[340: 19, 385: Away]`;
  - a slot ending at midnight gives `[1020: 19, 1439: Away]`;
  - a slot starting at midnight gives `[0: 19, 385: Away]`;
  - the existing tests that assume a leading Away entry (the happy path, "nothing is
    written when the built week equals Hive's", the midnight end and the midnight start)
    are updated to the new shape, and the fixture week drops its 00:00 entry so it
    matches the form Hive really returns.
- **`HiveSource.write_comfort_schedule`** with the fake Hive
  (`test_comfort_schedule_hive_write.py`): "first entry not at midnight" leaves the
  parametrised list of refused weeks, and a week whose days have no 00:00 entry is sent
  and verified.
- Prior art: the #808 tests in those two files, and the dry-run refusal tests that use
  "monday does not start at midnight" only as a sample message from a faked
  `WeekRuleError` (they keep working because the message is faked, not produced).

## Out of Scope

- Splitting a slot that doesn't lie within its day across two days (unchanged; it still
  keeps Hive's entries with a WARNING).
- Writing more than today and tomorrow, or changing the five days the writer leaves
  alone.
- Choosing the overnight temperature before a written day's first entry: it is the
  previous day's last setting, as in Hive.
- Merging adjacent plan slots in one day.

## Further Notes

- Found live on 2026-11-21: with `comfort_schedule.control: on` on the Pi, every poll
  logged `Comfort Schedule write failed: ValueError: monday does not start at midnight`.
  The newest stored Hive week has no 00:00 entry on any of the seven days.
- After deploy, the first `on` poll should write (outcome `applied` or `not_verified`)
  rather than `failed`. Check its read-back before relying on it, as the #808 rollout
  says.

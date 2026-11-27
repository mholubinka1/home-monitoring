# Issues: feature-comfort-windows-per-weekday

## Comfort Windows per weekday

**GitHub issue**: #839

**Blocked by**: None

**User stories**: 1, 2, 3, 8, 9

### What to build

`ComfortWindows` becomes seven named days, `monday` to `sunday`, each a start and an end, where an end at or before the start is the next morning. The defaults are 07:30–23:00 Monday to Thursday, 07:30–00:30 Friday, 08:30–00:30 Saturday and 08:30–23:00 Sunday. A day left out takes its default, and the old `weekday`/`weekend` keys are refused at startup. The plan looks up the named day, so Friday's stored plan slot ends at 00:30 Saturday. The README and the config template describe the new shape.

### Acceptance criteria

- [x] Given no `comfort_windows` in the config, when settings load, then Monday to Thursday are 07:30–23:00, Friday 07:30–00:30, Saturday 08:30–00:30 and Sunday 08:30–23:00.
- [x] Given a config setting only `friday`, when settings load, then Friday takes it and every other day keeps its default.
- [x] Given a config with the old `weekday` key, when settings load, then startup is refused with a message naming the key.
- [x] Given a Friday with the default windows, when the room forecast runs, then Friday's stored plan slot ends at 00:30 on Saturday; and on a Sunday the slot ends at 23:00.
- [x] Given the README and the config template, when read, then they describe the seven per-day windows and the next-morning end.

---

## hive-app writes a slot that crosses midnight

**GitHub issue**: #841

**Blocked by**: #839

**User stories**: 4, 8

### What to build

hive-app reads the plan slots for yesterday, today and tomorrow, and writes each day from every slot that overlaps it. A slot ending after the next midnight gives its own day the target at the start and the Away Temperature at 23:59. It gives the next day the target at 00:00 and the Away Temperature at the end, before that day's own slots. octopus-app keeps yesterday's plan slot stored for the whole of today. A slot that starts before its day is still refused.

### Acceptance criteria

- [ ] Given Friday's plan slot from 06:50 to 00:30 and Saturday's own slot, when hive-app writes on Friday, then Friday has the target at 06:50 and the Away Temperature at 23:59, and Saturday has the target at 00:00, the Away Temperature at 00:30, then its own slot.
- [ ] Given it is Sunday and Saturday's slot ended at 00:30 on Sunday, when hive-app writes, then Sunday starts with the target at 00:00 and the Away Temperature at 00:30.
- [ ] Given Friday's window has closed, when the room forecast runs on Saturday, then Friday's plan slot is still stored.
- [ ] Given a slot that starts before its day, when hive-app writes, then that day keeps Hive's entries and the reason is logged.

---

## The tail is the evening's evidence

**GitHub issue**: #840

**Blocked by**: #839

**User stories**: 5, 6, 7, 9

### What to build

The comfort job reads each day's polls up to the later of midnight and its window's close. Held Boosts and Quiet Days use the whole window, including the part after midnight. Minutes below comfort and the day's own Boost Starts stay on the calendar day. A day is settled once written at or after the later of its next midnight and its window's close, and each run starts from the oldest unsettled day. The explainer describes the per-weekday windows and the after-midnight tail.

### Acceptance criteria

- [ ] Given Friday held at 17.0 and a boost at 00:15 on Saturday with the room at 16.8, when the job runs after 00:30, then Friday is raised at 00:15 and Saturday starts at 17.5.
- [ ] Given Friday's row was written at 00:04 on Saturday, when the first run after 00:30 happens, then Friday is rewritten and settled, and later runs do not rewrite it.
- [ ] Given Friday held at home with the heating on for 90 minutes before midnight and 30 minutes after, and no boost, when the job runs after 00:30, then Friday is a Quiet Day.
- [ ] Given polls at 16.0 between 00:00 and 00:30 on Saturday in a counted hour, when the job runs, then they count towards Saturday's minutes below, not Friday's.
- [ ] Given the explainer, when read, then it describes the per-weekday windows and the after-midnight tail.

---

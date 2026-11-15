# Issues: bugfix/room-forecast-sub-hour-slots

## Current schedule follows slots to the minute

**GitHub issue**: #782

**Blocked by**: None

**User stories**: 1, 5, 6

### What to build

Split a forecast hour into parts wherever a target changes inside it, and step the hourly thermal model through the parts: the hour's drift without heating is spread evenly, and each part heats the least that reaches its target, at most its own length. Use it for the current-schedule forecast, taking the parts from the Hive slot start times. An hour with no change inside it is one part and gives the same numbers as before.

### Acceptance criteria

- [x] Given a slot at 19 C from 05:40 to 06:25 and a room at 16 C, when the job runs, then the 05:00 hour stores 20 minutes of heating and the 06:00 hour 25, and nothing after.
- [x] Given those minutes, when the rows are stored, then kWh and cost follow them.
- [x] Given a room that reaches a slot's target part-way through the slot, when it is forecast, then heating stops at the target.
- [x] Given two slot changes inside one hour, when it is forecast, then each part heats for at most its own length.
- [x] Given a schedule whose slots all start on the hour, when the job runs, then the stored rows are the same as before.
- [x] Given Hive's mode is OFF, when the job runs, then no hour heats.

---

## Comfort plan exact at the window's edges

**GitHub issue**: #783

**Blocked by**: #782

**User stories**: 2, 3, 5, 6

### What to build

Make the comfort plan use the same parts: each window's target is held from its opening minus a lead to its closing minute, where the lead is the shortest that has the room at the target at the opening minute. This replaces the separate pre-heat code, the interpolation inside the opening hour and the unheated closing hour.

### Acceptance criteria

- [ ] Given a window opening at 06:30 and a cold room, when the plan is made, then the room is at the target at 06:30 and no pre-heat minute falls after 06:30.
- [ ] Given the window has opened, when the plan is made, then the target is held from 06:30, within the 06:00 hour.
- [ ] Given a window closing at 23:30, when the plan is made, then the 23:00 hour holds the target until 23:30 and heats nothing after.
- [ ] Given a room already at the target at the opening, when the plan is made, then there is no pre-heat.
- [ ] Given a target out of reach, when the plan is made, then heating runs from the forecast's start.
- [ ] Given a window on whole hours, an overnight window after midnight, and weekday and weekend days, when the plan is made, then each behaves as before.

---

## First hour starts at the newest reading

**GitHub issue**: #784

**Blocked by**: #782

**User stories**: 4, 6

### What to build

Start the forecast at the newest reading's time instead of the top of the hour: only the parts of the first hour after the reading are stepped, for both scenarios, so heating that has already happened is not counted again. Heating seen before the reading in that hour still counts towards the heat carried into the next hour.

### Acceptance criteria

- [ ] Given a reading taken just after a slot has ended, when the job runs, then the rest of that hour stores no heating.
- [ ] Given a reading taken inside a slot, when the job runs, then the first hour stores only the minutes from the reading to the slot's end or the hour's end.
- [ ] Given heating seen before the reading in the first hour, when the second hour is forecast, then the heat carried into it counts those minutes plus the forecast remainder.
- [ ] Given a reading in the previous hour, when the job runs, then the rows start at that hour with only its remaining minutes.
- [ ] Given either scenario, when the job runs, then the first row's room is the reading.

---

## Guide and known limits

**GitHub issue**: #785

**Blocked by**: #783

**User stories**: 7

### What to build

Bring the heating guide's section on the room forecast into line with the new rules, and record the MANUAL mode limit.

### Acceptance criteria

- [ ] Given `docs/heating-model.md` section 12, when read, then the worked example is a half hour of pre-heat ending at 06:30 and the text says the target is held from the opening minute.
- [ ] Given the guide, when read, then it says MANUAL mode is forecast as if it were SCHEDULE.

---

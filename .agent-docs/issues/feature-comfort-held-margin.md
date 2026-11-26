# Issues: feature-comfort-held-margin

## A Held Boost needs the room below the comfort temperature

**GitHub issue**: #836

**Blocked by**: None

**User stories**: 3, 4, 5, 6

### What to build

The held-boost rule's room check becomes "below the Comfort Temperature by no more than `HELD_MARGIN`" (16.5 to 16.9 at 17.0). A boost with the room at or above the comfort temperature no longer raises it; a warm-up boost more than 0.5 °C below still doesn't. The explainer's held-boost bullet says so.

### Acceptance criteria

- [x] Given 17.0 held and a boost with the room at 17.0, when the job runs, then it is not held and today is not raised.
- [x] Given 17.0 held and a boost with the room at 16.9, when the job runs, then today is raised at that boost.
- [x] Given 17.0 held and a boost with the room at exactly 16.5, when the job runs, then today is raised.
- [x] Given the explainer, when read, then the held-boost bullet says the room was below the comfort temperature by no more than half a degree.

---

## Minutes below comfort use the held margin

**GitHub issue**: #837

**Blocked by**: None

**User stories**: 1, 2, 5, 6, 7

### What to build

Minutes below comfort count a reading only when the room is more than `HELD_MARGIN` below the Comfort Temperature in force at it: below 16.5 at 17.0, below 17.0 after a raise to 17.5. The Minutes Below Comfort panel description, its section in the queries document and the explainer describe the threshold.

### Acceptance criteria

- [x] Given 17.0 and readings at 16.6 in a counted hour, when the job runs, then they add no minutes below.
- [x] Given 17.0 and readings at 16.4 in a counted hour, when the job runs, then each adds 2 minutes below.
- [x] Given a raise to 17.5, when the job runs, then a later reading at 16.9 counts and one at 17.2 does not.
- [x] Given the panel description, the queries document and the explainer, when read, then they describe minutes below as more than half a degree below the comfort temperature in force.

---

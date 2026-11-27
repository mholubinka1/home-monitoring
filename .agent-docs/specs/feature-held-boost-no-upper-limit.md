# A Held Boost has a lower limit only

## Problem Statement

Since `feature-comfort-held-margin`, a Held Boost needs the room strictly below the Comfort Temperature (16.5 to 16.9 at 17.0). The household boosted inside a held window with the room at 17.2 against 17.0, meaning "17 is too cold even when the room reads 17.2". The boost raised nothing, so the house kept planning a temperature the household had just said was too cold.

## Solution

A Held Boost needs the room no more than `HELD_MARGIN` (0.5 °C) below the Comfort Temperature, with no upper limit. At 17.0, a boost with the room at 16.5, 17.0, 17.2 or 18.0 raises it; one at 16.4 (a late warm-up) does not. Every other Held Boost condition is unchanged, and minutes below comfort keep their 0.5 °C margin.

A day not yet settled is rewritten on the next hourly run, so a boost earlier that day that is held under this rule raises it from that boost.

## User Stories

1. As the household, I want a boost while the house is holding the comfort temperature to raise it even when the room reads at or above it, so that "too cold at this temperature" is heard.
2. As the household, I want a boost with the room more than half a degree below the comfort temperature still not to raise it, so that a late warm-up is not read as "too cold".
3. As a maintainer, I want the specs, glossary, ADR-0040 and explainer to state the lower limit only, so that they match the code.

## Implementation Decisions

- **Comfort module (octopus-app `data/comfort`).** The Held Boost room check goes back to `room >= comfort_temp - HELD_MARGIN`, with no upper bound. The docstrings of `_held_before` and `held_boost_start` are reworded to match. `HELD_MARGIN` keeps both its uses.
- **No other code change.** That covers minutes below comfort, Quiet Days, the plan, hive-app and the schema.
- **Docs.** Update `feature-comfort-held-margin.md` and `feature-comfort-temperature-half-degree-steps.md` where they state the strict upper bound, the glossary's Held Boost entry, a dated note on ADR-0040, and the held-boost bullet in `docs/heating-model.md`.

## Testing Decisions

One seam, `ComfortDayJob.refresh(now)`, in `tests/test_comfort_day_job.py`:

- The test that pins "a boost with the room at the comfort temperature is not held" is replaced: a boost at 17.0 against 17.0 is held.
- A new test: a boost at 17.2 against 17.0 is held.
- The pins for 16.9 and 16.5 (held) and 16.4 (not held) stay.

## Out of Scope

- Changing the margin, minutes below comfort or any other rule.
- Restating settled days.

## Further Notes

- This reverses only the upper bound added by `feature-comfort-held-margin`; its minutes-below margin stands.

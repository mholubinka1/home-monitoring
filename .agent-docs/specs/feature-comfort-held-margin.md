# Minutes below comfort and Held Boosts share the 0.5 °C held margin

## Problem Statement

Since the Comfort Schedule began holding the room, two comfort rules disagree with how a held room behaves.

- **Minutes below comfort overstate discomfort.** A reading counts as below comfort when the room is strictly below the Comfort Temperature in force. The thermostat holds the room within about 0.2 °C either side of its target, so a room held at 17.0 reads 16.9 for long stretches. On 2026-11-25, 122 of the day's 302 minutes below comfort were 16.8 or 16.9 while Hive held 17.0.
- **A Held Boost can raise a room that is already warm enough.** A boost counts as held whenever the room is no more than 0.5 °C below the Comfort Temperature, including at or above it. A boost pressed while the room is at 17.0 or warmer is not evidence that 17.0 is too cold.

## Solution

Both rules use the same margin, `HELD_MARGIN` (0.5 °C), so they never drift apart:

- **Minutes below comfort** count a reading only when the room is more than 0.5 °C below the Comfort Temperature in force at that reading. At 17.0 that is below 16.5. After a raise to 17.5 it is below 17.0.
- **A Held Boost** needs the room no more than 0.5 °C below the Comfort Temperature, and strictly below it: at 17.0, a room of 16.5 to 16.9. (Superseded: the upper limit was removed by [feature-held-boost-no-upper-limit](feature-held-boost-no-upper-limit.md); only the 0.5 °C lower limit stands.) A boost at 17.0 or warmer no longer raises it. A boost below 16.5 during a late warm-up still doesn't either.

Days already settled keep their stored figures. The new rules apply from the next row the hourly job writes (today's row, which is rewritten every hour).

On the 2026-11-25 data, minutes below comfort would have been 62, not 302.

## User Stories

1. As the household, I want minutes below comfort to count only readings more than half a degree below the comfort temperature, so that the thermostat's normal swing around its target isn't reported as discomfort.
2. As the household, I want the minutes-below threshold to follow a raise (below 17.0 once the comfort temperature is 17.5), so that the figure matches what the house was asked to hold.
3. As the household, I want a boost to raise the comfort temperature only when the room was below it, so that a boost pressed in a room already at temperature doesn't make the house dearer.
4. As the household, I want a boost during a late warm-up (room more than half a degree below) still not to raise it, so that a slow morning is not read as "too cold".
5. As a maintainer, I want both rules to use the one `HELD_MARGIN` constant, so that changing the margin changes both.
6. As a maintainer, I want days settled before the change kept as stored, so that past figures never change.
7. As the household, I want the dashboard description, the queries document and the explainer to describe the new thresholds, so that the panel reads correctly.

## Implementation Decisions

**Comfort module (octopus-app `data/comfort`).**

- `HELD_MARGIN` stays 0.5 and now serves both rules; its comment says so.
- **Held Boost.** The room check in the held-boost rule becomes `comfort_temp - HELD_MARGIN <= room < comfort_temp`, where `comfort_temp` is the value the day started at (the held-poll check already compares Hive's target with it). The other conditions are unchanged: a Boost Start inside the Comfort Window, the poll just before it held, online, and no more than `BOOST_GAP` earlier.
- **Minutes below comfort.** A reading counts when `room < value_in_force - HELD_MARGIN`, where `value_in_force` is the day's `comfort_temp` before `raised_at` and `comfort_temp + COMFORT_STEP` from it on. The other conditions are unchanged: counted hours only, not during No One Is Home, offline polls left out.
- No schema change, no client change, no change to the Quiet Day rule, the plan or query D.

**Docs.** The parent spec (`feature-comfort-temperature-half-degree-steps.md`), the glossary's Held Boost and Comfort Day entries, and a dated note on ADR-0040 are updated with this spec. The Minutes Below Comfort panel description in `data/grafana/gas-heating-dashboard.json`, its section in `data/grafana/mariadb/gas-heating-queries.md`, and section 11 of `docs/heating-model.md` follow with the code. The dashboard change is description text only, so the import can wait for the next one.

## Testing Decisions

One seam: `ComfortDayJob.refresh(now)` against the SQLite `mariadb_client` fixture, asserting the stored `comfort_day` rows, as in `tests/test_comfort_day_job.py`. No unit tests of the pure functions.

- A boost with the room at the comfort temperature (17.0 against 17.0) is not held.
- A boost at 16.9 against 17.0 is held, and so is one at exactly 16.5.
- A reading at 16.6 against 17.0 adds no minutes below; one at 16.4 does.
- After a raise to 17.5, a reading at 16.9 counts and one at 17.2 doesn't.
- Existing minutes-below scenarios that used a cold reading of 16.5 against 17.0 move to 16.4, so they keep testing what they were written for; held-boost scenarios already sit inside the new 16.5-to-16.9 band and stay as they are.

## Out of Scope

- Changing the margin's value, or making it configurable.
- Restating settled days.
- Any change to Quiet Days, the step-down, the floor, the plan, query D or the schema.

## Further Notes

- Readings are in tenths of a degree, so at 17.0 a Held Boost needs a room of 16.5, 16.6, 16.7, 16.8 or 16.9, and minutes below comfort start at 16.4.
- Worked examples in the repository use made-up values, never the household's real schedule times.

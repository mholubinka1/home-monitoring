---
status: accepted
---

# The Comfort Temperature steps half a degree on Held Boosts and Quiet Days

ADR-0034 learned the Comfort Temperature as the weighted median room temperature at Boost Starts. That only works while the room drifts freely. Once the Comfort Schedule held the room at the Comfort Temperature (live from 2026-11-24), every boost in the Comfort Window started at or above it, so new evidence could only lift the median. A comfortable household presses nothing, so it left no evidence that the room could be cooler. The value could rise and never fall, and its rounding up to Hive's half degree added a small lift of its own. We decided the Comfort Temperature is no longer learned from boost-start temperatures. It moves in 0.5 °C steps: up straight away after a **Held Boost** (at most one a day), and down from the next day after 3 **Quiet Days** since the last step. Days that are neither, and boosts that were not held, count for nothing. It never goes below a hardcoded 16 °C and has no ceiling. "Held" means Hive's own polled target equals the Comfort Temperature in force, so a write that never landed, a dry run, a manual dial change or the Away Override all count as not held. The stepped value replaces the median everywhere: in the Comfort Plan, the dashboard's comfort line and the minutes below comfort.

This is an up-down staircase (one up, three down), which settles where a day passes without a Held Boost with probability 0.5^(1/3) ≈ 0.79. That is a boost on about one day in five: the lowest temperature the household mostly puts up with, which is the cost-and-comfort balance the household chose.

## Considered Options

- **Keep the median and leave out held boosts.** That removes the upward bias but still gives no way down. The value would freeze, and with held boosts excluded the household could no longer say "too cold" at all.
- **A boost model with a cost trade-off.** Fit the chance of a boost at each room temperature from every poll at home, then pick the target that minimises heating cost plus a price per boost. It is the most direct form of the goal, but a held room sits at one temperature, so it cannot learn what happens half a degree lower without probing. The staircase supplies that spread, so this stays a possible next step.
- **Turning the thermostat down as a "too warm" signal.** It is cheap, but households rarely turn heating down; they put up with a warm room, which is exactly the hidden cost.
- **Keeping the median beside the stepped value on the dashboard.** Two "comfort" numbers to explain, and the median would still drift upward.
- **2, 5 or 7 Quiet Days per step.** These give a boost on about 29%, 13% or 9% of days. 3 was chosen for a cheaper house that learns faster.
- **A ceiling.** It was declined. Raises are limited to one a day, and three Quiet Days undo each one.
- **A configurable floor.** The 16 °C floor is a fixed safety limit, kept below UKHSA's suggested 18 °C minimum for living rooms but out of the range linked to damp and respiratory harm.
- **Checking "held" against hive-app's action log or config.** The log records writes sent, not held, and on 2026-11-21 writes logged as `applied` did not land. octopus-app cannot see hive-app's config.
- **Running the comfort job every 10 minutes.** A raise reaches Hive within the hourly cycle, mostly while the boost itself is still heating, so the extra load on the Pi is not earned.

## Consequences

- Days settled before the change keep their median values; the first stepped day carries on from the newest stored value rounded up to a half degree. With nothing stored (a fresh install) it starts at the 16 °C floor.
- A raise reaches Hive within about an hour: the comfort job and the plan run hourly, then hive-app picks up the plan on its next poll.
- With nothing for the household to set, the comfort level is found by probing: about every fourth day with heating and someone home, the house is held 0.5 °C cooler unless a Held Boost has pushed it back up.
- Summer, holidays, outages and dry runs never move it. They are not Quiet Days and cannot give a Held Boost.

## Note (2026-11-26): one margin for both rules

A Held Boost now also needs the room strictly below the Comfort Temperature (16.5 to 16.9 at 17.0): a boost in a room already at temperature is not evidence that it is too cold. Minutes below comfort count only readings more than the same 0.5 °C below the value in force, so the thermostat's normal swing of about 0.2 °C around its target is not reported as discomfort ([feature-comfort-held-margin](../specs/feature-comfort-held-margin.md)).

## Note (2026-11-27): a lower limit only

The upper limit added on 2026-11-26 is removed: a boost while the house holds the Comfort Temperature means it is too cold however warm the room reads, so a Held Boost needs only a room no more than 0.5 °C below it. The minutes-below margin stands ([feature-held-boost-no-upper-limit](../specs/feature-held-boost-no-upper-limit.md)).

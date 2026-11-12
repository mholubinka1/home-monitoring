---
status: accepted
---

# Comfort temperature is learned from the room temperature at boost starts

The gas dashboard needs a "comfortable" room temperature that is not the thermostat target: the target sits at the 7 C frost setting except for a short morning schedule, so "minutes below target" showed 0 almost every day. We decided octopus-app learns a single **comfort temperature** per London day as the median room temperature at the moment a boost starts, over the 90 days before that day (at least 5 boost starts; with fewer, the previous day's value carries on, and before any value exists nothing is shown). It also learns the day's **counted hours**: the London clock hours in which the heating was on at least once in the same 90 days, read from `heating_hour` (ADR-0033). An hourly job stores one `comfort_day` row per London day with the comfort temperature, the counted hours and the minutes the room was below comfort within them. A finished day is settled on the first run after it ends and never restated.

On the data of 3 October to 12 November 2026 (13 boosts), the median boost-start room temperature was about 17.0 C, while the boosts asked for 18 to 20 C and never reached it (15 to 45 minutes lifted the room by about 0.5 C).

## Considered Options

- **The boost target (about 20 C) as comfort.** It is the stated wish, but the room is almost never that warm, so nearly every minute of every day would count as below comfort.
- **A value per time band (afternoon, evening, night).** 3 to 5 boosts per band is too few for a steady number. One number now; bands are the known next step once there are a few dozen boosts per band.
- **A fixed sleep window instead of learned hours.** Simpler and predictable, but one more setting to maintain. The household chose learned hours, accepting that they are patchy while evidence is thin.
- **Restating history with the newest number.** Bars already seen would change, and early days would be judged by habits not yet formed. Rejected for a frozen row per day.
- **A dashboard override text box.** Would force the minutes to be counted in panel SQL over the raw 2-minute readings on every refresh (the Pi's database is capped at 512 MB). The `comfort_target` and `comfort_margin` variables are deleted.
- **Manual raises and schedule changes as evidence.** Only boosts are used: a schedule is a plan, not a reaction to feeling cold, and manual raises have only been seen in the 2026-10-06 test.

## Consequences

- The comfort temperature is the point where the household acts, so by construction about half of boosts start above it. No margin is applied.
- The 2026-10-06 test boost (room 20.7 C) is not excluded: a median absorbs one odd value, and it leaves the window in January 2027.
- A boost that follows another within 10 minutes is the same boost, and a Hive outage longer than 10 minutes inside a boost splits it in two.
- Counted hours come from `heating_hour`, which skips hours with too few polls, so an hour heated only during a poll outage is not counted.
- After a summer with no boosts the last learned value carries on until 5 new boosts fall inside the window.
- The thermostat is one room; comfort is that room's, not the house's.

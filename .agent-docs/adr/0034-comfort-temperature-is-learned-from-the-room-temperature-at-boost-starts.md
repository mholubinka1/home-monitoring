---
status: accepted
---

# Comfort temperature is learned from the room temperature at boost starts

The gas dashboard needs a "comfortable" room temperature that is not the thermostat target: the target sits at the 7 C frost setting except for a short morning schedule, so "minutes below target" showed 0 almost every day. We decided octopus-app learns a single **comfort temperature** per London day as the weighted median room temperature at the moment a boost starts, over the two years before that day (at least 5 boost starts; with fewer, the previous day's value carries on, and before any value exists nothing is shown). A boost's weight halves every 60 **heating days** (London days with the heating on at least once) that have passed since it, `0.5 ^ (heating days / 60)`, so recent boosts count more, the fade is smooth, and a summer with no heating ages nothing. It also learns the day's **counted hours**: the London clock hours in which the heating was on at least once in the 365 days before the day, read from `heating_hour` (ADR-0033). An hourly job stores one `comfort_day` row per London day with the comfort temperature, the counted hours and the minutes the room was below comfort within them. A finished day is settled on the first run after it ends and never restated.

On the data of 3 October to 12 November 2026 (13 boosts), the median boost-start room temperature was about 17.0 C, while the boosts asked for 18 to 20 C and never reached it (15 to 45 minutes lifted the room by about 0.5 C).

## Considered Options

- **A plain median over a fixed 90 days (the first design).** The value carried through summer and was then replaced by the first 5 boosts of autumn, and the counted hours emptied over summer. Rejected the day after it was written, before any deploy.
- **Equal weight over a year.** Steady, but slow to follow a real change; the household wanted recent boosts to count more.
- **Decay by calendar days.** Last winter would fade over the summer while nothing was learned. Decay counts heating days instead.
- **A half-life of 30 or 90 heating days.** At 30, last winter's 100 boosts weigh about 21 at the autumn restart, so 10 to 15 unusual boosts could move the value; at 90 a real change takes about two months to show. 60 was chosen.
- **The boost target (about 20 C) as comfort.** It is the stated wish, but the room is almost never that warm, so nearly every minute of every day would count as below comfort.
- **A value per time band (afternoon, evening, night).** 3 to 5 boosts per band is too few for a steady number. One number now; bands are the known next step once there are a few dozen boosts per band.
- **A fixed sleep window instead of learned hours.** Simpler and predictable, but one more setting to maintain. The household chose learned hours, accepting that they are patchy while evidence is thin.
- **Restating history with the newest number.** Bars already seen would change, and early days would be judged by habits not yet formed. Rejected for a frozen row per day.
- **A dashboard override text box.** Would force the minutes to be counted in panel SQL over the raw 2-minute readings on every refresh (the Pi's database is capped at 512 MB). The `comfort_target` and `comfort_margin` variables are deleted.
- **Manual raises and schedule changes as evidence.** Only boosts are used: a schedule is a plan, not a reaction to feeling cold, and manual raises have only been seen in the 2026-10-06 test.

## Consequences

- The comfort temperature is the point where the household acts, so by construction about half of the boosts' weight starts above it. No margin is applied.
- 100 boosts over a 180-heating-day winter carry a total weight of about 42 at the start of the next one; a consistent change of habit shows after about 5 weeks of heating days.
- The two-year cut-off only bounds what is read: a boost is dropped at about 1.6% weight.
- The 2026-10-06 test boost (room 20.7 C) is not excluded: a median absorbs one odd value, and it fades like any other boost.
- A boost that follows another within 10 minutes is the same boost, and a Hive outage longer than 10 minutes inside a boost splits it in two.
- Counted hours come from `heating_hour`, which skips hours with too few polls, so an hour heated only during a poll outage is not counted.
- An hour of the day stops being a counted hour a year after it was last heated.
- The thermostat is one room; comfort is that room's, not the house's.

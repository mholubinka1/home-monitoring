---
status: accepted
---

# The room model times heating at 2 minutes, learns at 10 and has a warm-up delay

The hourly room model ([ADR-0035](0035-room-forecast-and-comfort-plan-come-from-a-small-learned-thermal-model.md)) showed the room warming the moment heating started and stored one point an hour, so the Comfort Plan's pre-heat was mistimed and a 45-minute slot was hard to see. We decided on three grains, each set by its data. **Timing is at 2 minutes**, hive-app's poll interval: the forecast is run forward in 2-minute steps, and the Comfort Plan switches on and off on 2-minute marks. **Learning is at 10 minutes**: the Thermal Model's five numbers are fitted on 10-minute room changes (five polls), and the forecast is stored every 10 minutes. **Actual heating cost is at 30 minutes**, the gas meter's grain. The model also gains a **Warm-Up Delay**: the time between the thermostat calling for heat and the room responding, chosen at each daily refit as the shift of the heating record (0 to 40 minutes, in 2-minute steps) whose fit misses the readings least.

This amends ADR-0035 (hourly steps, no delay) and [ADR-0033](0033-hourly-heating-cost-learns-a-hot-water-baseline-and-the-boilers-effective-kw.md) (hourly heating cost). The rest of both stands: the five numbers, the backtest and its 0.5 C gate, the Comfort Plan as advice only, the hot-water baseline and the learned boiler kW.

The two tables that replace `heating_hour` and `room_forecast_hour` carry each row's own `starts_at` and `ends_at`, so a row of any length can be written, and dashboard SQL judges a day complete by the minutes its rows cover, never by a row count. A later change of grain is then a change of one constant, not of the schema or the panels.

## Considered Options

- **15-minute steps throughout, heating periods on quarter-hours** (the first request). Hive's readings are every 2 minutes, and the household wants the plan to say "on at 06:04, off at 06:44". A quarter-hour grid would round the plan by up to 14 minutes for no gain.
- **Learning at 2 minutes too.** The thermostat reports in 0.1 C steps and a room moves 0.01 to 0.05 C in 2 minutes, so most 2-minute changes read as zero and the fit would mostly learn rounding. Ten minutes is long enough to see a change and a whole number of polls, so model steps and readings stay aligned.
- **Storing the forecast every 2 minutes.** About 525,000 frozen day-ahead rows a year on a Pi whose MariaDB is capped at 512 MB. The any-length tables leave this open.
- **Timing each heating run** (minutes from heating-on to the first reading 0.2 C above the lowest since; the median over runs). Easy to picture and literally "when the temperature starts to rise", but rounding adds several minutes of bias, each run is noisy, and the number is not guaranteed to improve the forecast. The best-fitting shift uses every reading and is by construction the delay that serves the forecast; it is the method the daily Heating Model already uses for its threshold.
- **A fixed default delay, no forecast, or no delay when it cannot be learned.** The household chose to carry the last learned value; before any exists the model assumes no delay and says so.
- **Heating's effect cut off when a period ends.** The last minutes of gas would buy no warmth in the model. Heat already asked for keeps arriving for the delay after a period ends.
- **Actual heating cost at 10 or 15 minutes.** A half-hour meter reading would have to be shared between steps by minutes on, turning measured bars into estimates.
- **Writing finer rows into the hourly tables, or tables named for a new grain.** The names would lie, old and new rows would mix, and the next change of grain would need new tables again.
- **A period-length column in the key.** Rows of two grains for the same time would then coexist and every query would have to pick one. One row per start time, with its end, is enough.

## Consequences

- The Comfort Plan's pre-heat now starts earlier by the delay, then rounds earlier to a 2-minute mark. Comfort Window times are rounded earlier to a 2-minute mark as well.
- The forecast line stays on its unheated drift for the delay after a period starts, and peaks just after a period ends. Gas and cost are counted when the heat is asked for, not when it arrives.
- A delay is learned only when the 60-day window holds at least 10 heating starts; otherwise the newest learned delay carries on and the stored model says it was carried.
- Stored numbers stay in per-hour units (C per hour of heating, C per hour per degree), so fits before and after this change can be read side by side, though the carried gain now means the previous 10 minutes, not the previous hour, and is not comparable.
- The model is fitted from the thermostat polls directly; it no longer depends on the stored heating rows. The forecast still reads them for the boiler kW.
- `heating_hour` and `room_forecast_hour` stop being written and are dropped by hand after the dashboard review. Their few frozen day-ahead days are not carried over, so the Daily Cost panel's forecast lines restart. Rebuilt actual half-hours older than the 45-day Retention Window have no meter reading left and become estimated.
- The daily refit tries 21 delays, and so does each of the 14 backtest fits. It must stay within a few minutes on the Pi; the forecast job is unaffected.
- If hive-app's poll interval changes, the 2-minute timing step and the polls-per-step thresholds change with it (as for ADR-0033 and ADR-0035).

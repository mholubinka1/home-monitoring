# Issues: feature/room-model-two-minute-timing-warm-up-delay

## Actual heating cost in half-hours

**GitHub issue**: #793

**Blocked by**: None

**User stories**: 1, 2, 14, 15

### What to build

Store actual heating per half-hour, the gas meter's grain, in a new `heating_interval` table whose rows carry their own start and end, in place of `heating_hour`. The rules of ADR-0033 apply with the hour replaced by the half-hour (12 of 15 polls, a hot-water baseline per half-hour of the London day, boiler kW from measured half-hours with at least 10 minutes on). The existing backfill rule rebuilds the history from the thermostat polls on first run. Move the readers (comfort day job, the forecast job's boiler kW) to the new table. Rename the cost panel to Heating Cost per Half-Hour and point it at the new table; make the Daily Cost panel judge completeness by minutes covered, with the hourly forecast rows read as one-hour spans for now. Update the queries document with the JSON. See the spec, sections "Tables", "Actual heating in half-hours" and "Dashboard".

### Acceptance criteria

- [x] Given polls and half-hour meter readings, when the job runs, then one `heating_interval` row per half-hour is stored with its start, end, minutes on, kWh and cost.
- [x] Given a half-hour with fewer than 12 polls, when the job runs, then it has no row.
- [x] Given an empty `heating_interval` table and polls going back weeks, when the job first runs, then every half-hour since the first poll's local day is stored.
- [x] Given a rebuilt half-hour with no meter reading left, when it is stored, then its source is estimated.
- [x] Given the dashboard, when read, then the cost panel is titled Heating Cost per Half-Hour and reads `heating_interval`, and the Daily Cost panel judges each line complete by minutes covered.
- [x] Given heating in a half-hour, when the comfort day job runs, then that hour is a Counted Hour and the day a Heating Day.

---

## Room forecast in 2-minute steps, stored every 10 minutes

**GitHub issue**: #794

**Blocked by**: #793

**User stories**: 3, 4, 5, 6, 14, 15

### What to build

Rewrite the room forecast as a simulation in 2-minute steps on even clock minutes, replacing the parts-of-an-hour mechanism, and store one row per 10 minutes in a new `room_forecast_interval` table whose rows carry their own start and end. The simulation takes the carried-heat window from the model's step length (60 minutes for a model fitted hourly), so it runs correctly on the existing model. The current schedule follows the Hive slots as programmed; the Comfort Plan's lead is searched in whole 2-minute steps; Comfort Window times are rounded earlier to a 2-minute mark when the config is read. The gas unit rate is read once per distinct hour of a run. Point the Temperatures, Heating Plan and Room Model, Schedule Today and Daily Cost panels at the new table, with cost to midnight and day completeness by minutes covered. No warm-up delay yet (it is zero). See the spec, sections "Room forecast in 2-minute steps" and "Dashboard".

### Acceptance criteria

- [x] Given a slot at 19 C from 05:40 to 06:25 and a cold room, when the job runs, then the 05:40 to 06:10 rows each store 10 minutes of heating, the 06:20 row 4 (the 7 C slot starting at 06:25 takes effect from the 06:24 step), and no other row any.
- [x] Given a cold room and a window opening at 06:30, when the plan is made, then its slot starts on a 2-minute mark and the room is at the target at 06:30.
- [x] Given a comfort window configured at 07:45, when the app starts, then the window opens at 07:44 and the log says so.
- [x] Given a model fitted hourly, when the job runs, then a schedule on whole hours forecasts the same room at each hour as before.
- [x] Given a full day of weather for tomorrow, when the job runs, then day-ahead rows cover every minute of tomorrow.
- [x] Given a weather gap inside tomorrow, when the job runs, then the earlier day-ahead rows are left as they were.
- [x] Given the dashboard, when read, then the Temperatures, Heating Plan and Room Model, Schedule Today and Daily Cost panels read `room_forecast_interval`.
- [x] Given live rows that do not cover every minute to local midnight, when the Schedule Today query is read, then its cost to midnight is blank by a minutes-covered check.

---

## Thermal model learned on 10-minute steps

**GitHub issue**: #795

**Blocked by**: #794

**User stories**: 7, 8

### What to build

Fit the Thermal Model on 10-minute room changes read straight from the thermostat polls (room temperature, state and boost in one read), no longer from stored heating rows. Hourly weather is interpolated; inputs are scaled so the five stored numbers stay in per-hour units. The usability checks keep their order with new sizes (1,008 steps, 30 heating steps). The backtest replays each of the last 14 days in 10-minute steps; the 0.5 C gate is unchanged. Store `step_minutes` with each fit and refit at once when the newest stored model is not a 10-minute fit. The delay stays zero in this slice. See the spec, section "Thermal Model on 10-minute steps".

### Acceptance criteria

- [ ] Given 60 days of polls from a made-up house, when the model is fitted, then its five numbers, in per-hour units, match the house's.
- [ ] Given a step with no room reading within 4 minutes of its start, when the model is fitted, then that step is left out.
- [ ] Given under a week of complete steps, when the model is fitted, then it is stored unusable with the reason too few steps.
- [ ] Given under 30 heating steps, when the model is fitted, then it is stored unusable with the reason too little heating.
- [ ] Given the newest stored model was fitted hourly, when the job runs, then it refits at once and stores `step_minutes` 10.
- [ ] Given a backtest mean miss above 0.5 C, when the model is stored, then it is unusable and no new forecast is made.

---

## Warm-up delay learned, used and shown

**GitHub issue**: #796

**Blocked by**: #795

**User stories**: 9, 10, 11, 12, 13

### What to build

Learn the Warm-Up Delay at each refit: try delays of 0 to 40 minutes in 2-minute steps, shifting the heating record later by each, and keep the one whose fit misses the readings least. Store it as learned only when the window holds at least 10 Heating Starts; otherwise carry the newest stored delay, or zero with the source none. The backtest's earlier models use their own delay. The forecast then treats heat asked for at a time as arriving the delay later: nothing arrives for the first delay of a period, heat keeps arriving for the delay after it ends, and gas is counted when asked for. The Comfort Plan's lead therefore includes the delay. Add the delay figure and its state to the Heating Plan and Room Model panel. Measure the refit's run time on the Pi's data volume; it must finish within 5 minutes. See the spec, sections "Thermal Model on 10-minute steps" and "Room forecast in 2-minute steps".

### Acceptance criteria

- [ ] Given polls from a made-up house whose room responds 14 minutes after heating starts, and at least 10 Heating Starts, when the model is fitted, then the stored delay is 14 and its source learned.
- [ ] Given fewer than 10 Heating Starts and an earlier stored delay of 14, when the model is fitted, then the delay is 14 and its source carried.
- [ ] Given fewer than 10 Heating Starts and no earlier delay, when the model is fitted, then the delay is 0 and its source none.
- [ ] Given a delay of 14 and a slot from 05:40 to 06:25, when the job runs, then heating minutes are stored from 05:40, the room follows its unheated course until 05:54, and heat keeps arriving until 06:38.
- [ ] Given a delay of 14, when the plan is made, then its slot starts 14 minutes earlier than with no delay and the room is at the target at the opening.
- [ ] Given the dashboard, when read, then the Heating Plan and Room Model panel shows the delay in minutes and whether it is learned, carried or not learned yet.

---

## Guide, queries document and retired tables

**GitHub issue**: #797

**Blocked by**: #796

**User stories**: 16

### What to build

Bring the heating guide into line with the new rules, record the first learned delay as the assessment the household asked for, and list the retired hourly tables for removal by hand after the dashboard review.

### Acceptance criteria

- [ ] Given `docs/heating-model.md`, when read, then sections 10 and 12 describe half-hourly cost, 10-minute learning, 2-minute timing and the warm-up delay with a worked example.
- [ ] Given the guide after the first refit with a learned delay, when read, then it records the delay, its source and the number of Heating Starts.
- [ ] Given the queries document, when read, then it lists `heating_hour` and `room_forecast_hour` as retired, with the statements to drop them by hand after the dashboard review.

---

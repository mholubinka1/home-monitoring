# Issues: feature/hourly-room-forecast-comfort-plan-e3

## Room forecast under the current schedule

**GitHub issue**: #774

**Blocked by**: None

**User stories**: 1, 4, 5, 8, 11

### What to build

The `room_forecast_hour` table, the hive-app table mirrors, and `RoomForecastJob.refresh(now)`: run the thermal model forward from the newest reading to the end of tomorrow under the Hive schedule as programmed, holding each slot's target with the least heating, and store live rows with heating minutes, kWh and cost per hour.

### Acceptance criteria

- [x] Given a usable model, a weekly schedule, an hourly weather forecast and a fresh reading, when the job runs, then live `current` rows run from the hour containing now to the end of tomorrow, starting from the newest reading.
- [x] Given a slot target above the room, when its hour is forecast, then the heating fraction is the smallest that holds the target (0 to 1) and `on_minutes` follows it.
- [x] Given heating minutes, when an hour is stored, then `heating_kwh` is the minutes over 60 times the learned boiler kW and `cost_gbp` uses the gas unit rate, and is empty without a rate.
- [x] Given Hive's mode is OFF, when the job runs, then the current-schedule forecast has no heating.
- [x] Given an unreadable or missing weekly schedule, when the job runs, then no `current` rows are stored and a warning is logged.
- [x] Given a second run, when it stores, then the earlier live rows are replaced.

---

## Forecast guards and clock changes

**GitHub issue**: #775

**Blocked by**: #774

**User stories**: 6, 7, 12

### What to build

Make the forecast silent, and never wrong, when its inputs are not good enough: an unusable model, a stale reading, missing or partial weather, a NULL radiation, a 23- or 25-hour day and tables missing from hive-app.

### Acceptance criteria

- [x] Given the newest `thermal_model` is unusable and an older one is usable, when the job runs, then nothing is stored, the existing live rows are left as they are, and the older model is not used.
- [x] Given the newest reading is more than 30 minutes old, when the job runs, then nothing is stored.
- [x] Given no hourly weather for the first hour, when the job runs, then nothing is stored; and given a gap later, then the forecast stops at the gap.
- [x] Given a NULL radiation for an hour, when it is forecast, then sunshine gain is zero and the hour is kept.
- [x] Given radiation, when an hour is forecast, then the temperature at stamp `h` is paired with the radiation at stamp `h + 1`.
- [x] Given tomorrow is a 23- or 25-hour local day, when the job runs, then the horizon covers exactly those hours.
- [x] Given a missing hive-app table, when the job runs, then it reads as no rows and does not raise.

---

## Comfort plan

**GitHub issue**: #776

**Blocked by**: #774

**User stories**: 2, 3, 4, 10, 14

### What to build

The comfort windows in the octopus-app config, and a second forecast under a comfort plan: the comfort temperature rounded up to 0.5 C, held inside the window, with the shortest pre-heat that reaches it by the window's start.

### Acceptance criteria

- [x] Given no `comfort_windows` setting, when settings load, then weekdays are 06:30 to 00:00 and weekends 07:45 to 00:00; and given a bad time, then startup stops.
- [x] Given a comfort temperature of 19.3, when the plan runs, then the target is 19.5 held from the window's start to midnight.
- [x] Given a cold room before a window, when the plan is made, then the pre-heat is the shortest run of full heating that reaches the target by the window's start, with the room read by linear interpolation within the hour.
- [x] Given weekday and weekend days, when the plan is made, then each uses its own window.
- [x] Given a day with no comfort temperature, when the job runs, then no `plan` rows are stored for it and the `current` rows are still stored.
- [x] Given plan rows, when stored, then they carry minutes, kWh and cost like the current ones.

---

## Day-ahead freeze

**GitHub issue**: #778

**Blocked by**: #774, #776

**User stories**: 9

### What to build

Store a day-ahead copy of tomorrow's forecast for both scenarios, rewritten while the day is still tomorrow and never written once it has begun.

### Acceptance criteria

- [x] Given a run today, when it stores, then `day_ahead` rows are written for tomorrow's hours only, in both scenarios.
- [x] Given further runs while that day is still tomorrow, when they store, then tomorrow's day-ahead rows are rewritten.
- [x] Given midnight has passed, when the job runs, then that day's day-ahead rows are unchanged and none are written for today or the past.

---

## Room forecast in the hourly chain

**GitHub issue**: #777

**Blocked by**: #774

**User stories**: 13

### What to build

Run the room forecast job in the hourly chain after the thermal model, with its own job record, skipped when the heating-hour run failed.

### Acceptance criteria

- [x] Given a successful heating-hour run, when the chain runs, then the room forecast runs after the thermal model with its own `room_forecast_refresh` job record.
- [x] Given a failed heating-hour run, when the chain runs, then the room forecast is recorded as skipped.
- [x] Given a failed comfort refresh, when the chain runs, then the room forecast still runs.

---

## Dashboard: forecast lines and plan panel

**GitHub issue**: #779

**Blocked by**: #774, #776

**User stories**: 6, 15

### What to build

Panel 1 gains two dotted lines (the live current and plan forecasts). One new panel shows heating minutes per hour for current against plan, their costs, the backtest error, and whether the newest model is flawed and how old the forecast is.

### Acceptance criteria

- [x] Given the dashboard JSON, when it loads, then panel 1 has two dotted series, the live current and live plan room temperatures.
- [x] Given the JSON, when it loads, then a new panel shows heating minutes per hour for current against plan, the two costs and the newest backtest mean and worst error.
- [x] Given the newest `thermal_model` is unusable, when the new panel is viewed, then it shows its unusable reason and the age of the live forecast.
- [x] Given the queries, when read, then they use the data source's Database setting and no heavy aggregation.

---

## Docs: heating guide and glossary

**GitHub issue**: #780

**Blocked by**: #774, #776

**User stories**: 15

### What to build

Explain the room forecast and the comfort plan in plain language in the heating guide, and list the new table in the glossary.

### Acceptance criteria

- [ ] Given `docs/heating-model.md`, when read, then section 12 explains the forecast and the plan in plain language.
- [ ] Given the glossary, when read, then the `home_monitoring` database entry lists `room_forecast_hour`.

---

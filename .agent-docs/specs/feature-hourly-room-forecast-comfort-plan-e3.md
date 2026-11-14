# Room forecast, comfort plan and dashboard (E3)

## Problem Statement

The household wants to see what the room will do to the end of tomorrow, and what heating would keep it comfortable ([ADR-0035](../adr/0035-room-forecast-and-comfort-plan-come-from-a-small-learned-thermal-model.md)). E1 (merged) stores the hourly weather forecast and the weekly Hive schedule; E2 (merged) learns and backtests the **Thermal Model**. Nothing yet runs the model forward, so the dashboard shows only what has already happened.

This is the last of three pull requests for workstream E.

## Solution

An hourly octopus-app job runs the **Thermal Model** forward, hour by hour, from the room's newest reading to the end of tomorrow, twice: under the Hive schedule as programmed, and under a **Comfort Plan** that holds the **Comfort Temperature** inside the **Comfort Window**. Each hour is stored with its room temperature, heating minutes and cost. Live rows are replaced on every run; **Day-Ahead Forecast** rows freeze at midnight. Panel 1 of the gas and heating dashboard gains two dotted lines, and one new panel shows the heating minutes and cost of the current schedule against the plan, with the backtest error. Nothing is ever written to Hive.

Terms are in `.agent-docs/context.md`.

## User Stories

1. As the household, I want a room forecast to the end of tomorrow under my Hive schedule as programmed, so that I can see whether the house will be warm when I need it.
2. As the household, I want a second forecast under a comfort plan, so that I can see what heating would keep the room comfortable and compare it with what I have now.
3. As the household, I want the plan to hold the comfort temperature, rounded up to the half degree Hive accepts, from 06:30 on weekdays and 07:45 at weekends to midnight, with the shortest pre-heat that gets there by the start, so that it is the least heating that is comfortable.
4. As the household, I want heating minutes and cost per hour for both forecasts, so that I can see what the plan would save or cost.
5. As the household, I want the cost to use the boiler's learned kW and the gas unit rate, so that it is the same estimate as the Heating Cost per Hour panel.
6. As the household, I want no new forecast stored when the thermal model is unusable, and the dashboard to show that the model is flawed and how old the lines are, so that a stale forecast is never mistaken for a current one.
7. As the household, I want nothing forecast from stale data (no recent thermostat reading, no hourly weather for the hours ahead), so that the line never starts from a guess.
8. As the household, I want the current-schedule forecast to assume no heating when Hive's mode is OFF, so that it shows what will really happen.
9. As the household, I want a forecast row for a day to freeze when the day begins, so that I can compare the day-ahead line with the measured room.
10. As the household, I want the plan silent on days with no comfort temperature, so that it does not invent one, while the current-schedule forecast is still drawn.
11. As a maintainer, I want a missing or unreadable weekly schedule to leave out the current-schedule forecast and not to be read as "no heating", so that a Hive payload change cannot draw a wrong line.
12. As a maintainer, I want a forecast hour with no sunshine value (NULL) to be forecast with no sunshine gain and not dropped, so that one missing number does not blank the forecast.
13. As a maintainer, I want the forecast to run in the hourly chain after the thermal model, with its own job record, skipped when the heating-hour run failed, so that its failures are visible and it never runs on half-written inputs.
14. As a maintainer, I want the comfort windows in the octopus-app config with the agreed defaults, so that they can change without code.
15. As a maintainer, I want the heating guide, glossary and dashboard to describe and show the forecast, so that it can be understood and challenged later.

## Implementation Decisions

**Modules built or changed** (all octopus-app unless noted): `data/thermal.py` (reused, plus forecast and plan functions in a new `data/room_forecast.py`), new `data/room_forecast_job.py`, `data/mysql/model.py` and `data/mysql/client.py` (table, mirrors, reads, write), `data/model.py` (records), `common/config.py` (comfort windows), `main.py` (the hourly chain), `data/grafana/gas-heating-dashboard.json`, `docs/heating-model.md`, `.agent-docs/context.md`.

**Schema (Schema Sync, ADR-0005).** New table `room_forecast_hour`, primary key (`kind`, `scenario`, `hour_start`):

- `kind`: `live` or `day_ahead`. `scenario`: `current` or `plan`.
- `hour_start` (DateTime, naive UTC): the room figure is the predicted room at this hour's start, the same convention as the thermostat's hourly start readings, so it can be drawn against the measured room.
- `room_temp` (double), `on_minutes` (Integer), `heating_kwh` (double), `cost_gbp` (double, nullable without a gas rate), `computed_at` (DateTime).

**Cross-app mirrors.** `weather_forecast_hour` and `heating_schedule` are mirrored as Core `Table` objects on `weather_metadata` in the client, with the columns of hive-app's model (E1). A missing table reads as no rows, through the shared missing-table handling of `read_hourly_weather`. `read_hourly_weather_forecast(start, end)` returns the same `HourlyWeather` per stamp, radiation possibly None. `read_latest_heating_schedule()` returns the newest row's JSON. The newest `heating_status` row gives the room reading, its time and Hive's mode.

**The forecast steps.** The existing `thermal.replay` steps the model over recorded steps; the forecast needs the opposite, choosing the heating. A new pure function steps one hour from a room, a target and the hour's weather and previous heating. It reuses `ThermalNumbers`, `Step` and `ONE_HOUR`, not copies. The radiation stamp rule is the same as in `thermal.py`: temperature at stamp `h` pairs with radiation at stamp `h + 1` (ADR-0029). A NULL radiation counts as zero sunshine gain.

**Holding a target.** In an hour, the heating fraction is the smallest that keeps the room at the target: with the room's passive change (no heating) computed first, the fraction is (target minus room minus passive change) divided by the heating gain, clamped to 0..1. No target (OFF, or no heating wanted) is a fraction of 0. Heating minutes are the fraction times 60, rounded to whole minutes; `heating_kwh` is minutes over 60 times the learned boiler kW; cost is that times the gas unit rate in pence, over 100 (the arithmetic of `heating_hours.estimated_cost`, reused).

**Current schedule.** The hour's target is the target of the Weekly Schedule slot in force at the hour's start, by local day name and minutes after local midnight. Mode OFF gives no heating. A boost running at forecast time is ignored (ADR-0035). The schedule JSON is parsed defensively: anything that cannot be read as day names with slots of `start` minutes and `value.target` produces no current-schedule rows for that run (the plan is still stored), logged at WARNING, and the earlier live current-schedule rows are removed so that no stale line stays drawn. This is the household's stated leaning ("draw no current line"), to be confirmed once the first real `heating_schedule` row has been read: hive-app's fix for the library's added keys (PR #773) is merged but undeployed, so the real payload has not been seen.

**Comfort plan.** Per local day, the target is the comfort temperature of the newest `comfort_day` row up to today's that has one (today's own row when it has one, and for tomorrow too, as tomorrow has none yet; an older row stands in when today's has none) rounded up to the next 0.5 C, held from the window's start to its end (midnight by default). A day with no comfort temperature has no plan rows, and the earlier live plan rows are removed. (Only an unusable model, a stale reading or missing weather leave the earlier live rows in place, since then the whole forecast is stale and the panel says so.) The pre-heat is the shortest run of full heating that brings the room to the target by the window's start, found by stepping the model back from the start; outside the window and pre-heat the plan is not heating. The room at a start that falls inside an hour is read by linear interpolation within that hour. **The interpolation is my assumption.**

**Comfort windows.** `comfort_windows` in `ApplicationSettings`, with `weekday` and `weekend` each a start time and an end time, defaults 06:30 to 00:00 and 07:45 to 00:00. Weekend is Saturday and Sunday in local time. An invalid time stops startup like other bad config. A window whose end is at or before its start runs into the next local day (00:00 is midnight at the end of the day, and an overnight window such as 22:00 to 06:00 is accepted). The plan therefore also considers the window that began on the previous local day, so that a run after midnight holds the target until that window closes instead of treating those hours as pre-heat for the next one. An end time inside an hour (such as 23:30) leaves the hour it falls in unheated: only whole hours inside the window are held, since the room starts that hour at the target and drifts only a little before the window closes, and heating the whole hour would overstate its minutes and cost.

**Start and horizon.** The forecast runs from the hour containing now to the end of tomorrow's local day (23, 24 or 25 hours a day across a clock change). The newest thermostat reading is the room at the first hour's start; it must be no more than 30 minutes old. The first step's previous heating is the stored `heating_hour` minutes of the hour before; an absent row counts as zero. Hours with no hourly weather stop the forecast at the first gap; with no weather for the first hour nothing is stored.

**Model in force.** The newest `thermal_model` row; if it is unusable, nothing is stored and nothing is reused from an older row (ADR-0035). Existing live rows are left as they are in that case (agreed with the household: the last lines stay drawn), and are replaced on the next successful run. The dashboard shows that the model is flawed instead: the new panel reads the newest `thermal_model` row and shows whether it is usable, its `unusable_reason` and backtest errors, and the age of the newest live forecast (from `computed_at`), so a stale line is visibly stale. The model rows themselves are never touched.

**Storage.** Each successful run replaces every `live` row. `day_ahead` rows are written only for tomorrow's local day, and only while it is still tomorrow; a run rewrites tomorrow's day-ahead rows, so the last run before midnight is the one that freezes. A run replaces them only when its forecast covers every hour of tomorrow's local day (23, 24 or 25): a run cut short by a weather gap leaves the earlier day-ahead rows as they were, so a frozen day is never shortened. A day-ahead row is never written for today or the past.

**Scheduling.** `register_heating_hour_job` gains the room forecast job in its `dependants`, after the thermal model, with job name `room_forecast_refresh`; skipped and recorded when the heating-hour run failed. It does not depend on the comfort job's outcome. Hourly-chain tests go in `tests/test_hourly_heating_chain.py`.

**Dashboard.** `gas-heating-dashboard.json`: panel 1 (Indoor, Target and Outdoor Temperature) gains two dotted series, the live current-schedule and live plan room temperatures. A new panel shows heating minutes per hour for current against plan, the cost of each, the newest backtest mean and worst error, the newest model's status and unusable reason, and the age of the live forecast. Queries respect the data source Database setting and avoid heavy aggregation (the Pi's 512 MB cap). The frozen day-ahead line is stored for comparison against the measured room; drawing it is a follow-up for the final dashboard review.

**Docs.** `docs/heating-model.md` section 12 gains the forecast and the plan in plain language. The glossary's `home_monitoring` database entry lists `room_forecast_hour`.

**Deploy.** Recreate octopus-app on the Pi; Schema Sync creates the table. Check after the next hourly run: live rows for both scenarios cover now to the end of tomorrow, and `job_run` has `room_forecast_refresh`. It needs the three E tables populated.

## Testing Decisions

Good tests assert stored rows and returned values, not internals.

1. **The job seam.** `RoomForecastJob.refresh(now)` against the SQLite `mariadb_client` fixture, as `tests/test_thermal_model_job.py` does: seed a usable `thermal_model`, `heating_status`, `heating_schedule`, `weather_forecast_hour`, `comfort_day` and `heating_hour` rows, run with a fixed clock, assert `room_forecast_hour`.
2. **Unit tests of the maths** (agreed with the household): the one-hour step that holds a target, the heating fraction clamps, the pre-heat search, the comfort-window and schedule lookup, and the defensive schedule parser, in `tests/test_room_forecast.py`.
3. **Hourly chain:** `tests/test_hourly_heating_chain.py`.
4. **Dashboard JSON:** `tests/test_grafana_dashboards.py`.
5. **Config:** `tests/test_config_settings.py`.

Scenarios (each also a BDD red-green step):

- a usable model, a schedule and a weather forecast give live rows for both scenarios from now to the end of tomorrow;
- the forecast starts from the newest reading and a reading older than 30 minutes stores nothing;
- an unusable newest model stores nothing, leaves the existing live rows as they are, and does not reuse an older usable one;
- no hourly weather for the first hour stores nothing, and a gap later stops the forecast there;
- a NULL radiation is forecast as no sunshine;
- radiation is paired from the stamp one hour later;
- the current schedule's heating follows its slot targets, and mode OFF gives no heating;
- an unreadable or missing weekly schedule stores the plan rows and no current-schedule rows, and removes earlier live current-schedule rows;
- the plan holds the comfort temperature rounded up to 0.5 C inside the window, with the shortest pre-heat reaching it by the start, on weekdays and at weekends;
- a day with no comfort temperature has no plan rows, and earlier live plan rows are removed;
- heating minutes, kWh and cost follow the learned boiler kW and the gas unit rate, and cost is empty without a rate;
- live rows are replaced on every run;
- day-ahead rows are written for tomorrow, rewritten while it is still tomorrow, and frozen after midnight;
- a day across a clock change forecasts 23 or 25 hours;
- a missing hive-app table reads as no rows and does not raise;
- the job runs in the hourly chain after the thermal model, is skipped after a failed heating-hour run, and still runs after a failed comfort refresh;
- the comfort windows default and can be configured, and a bad time stops startup;
- the dashboard has the two dotted series on panel 1 and the new panel, and still loads.

## Out of Scope

- Any write to Hive.
- A stored score per day (the day-ahead line is compared with the measured room instead).
- Drawing the day-ahead line, which waits for the final dashboard review.
- Seven or three days ahead.
- Choosing between model forms, or reusing an older usable model.
- A per-PR Grafana review: one review happens at the end, after F and G.
- Cost-based gas change panel (F) and flow-temperature model (G).

## Further Notes

- Thin evidence (ADR-0035): nothing colder than about 1 C outdoors has been seen, and 21 to 27 October 2026 has no thermostat data. On 13 November the real model was usable (backtest mean 0.26 C, worst 0.97 C over 8 of 14 days).
- The weekly schedule's payload has never been seen raw. The first stored `heating_schedule` row is the check, so the parser must be defensive.
- If hive-app's poll interval changes, the 30-minute reading age and the heating-hour thresholds change with it (ADR-0033).
- Cost carries the ADR-0033 caveat: thermostat demand is not proof the burner fired throughout.
- The interpolation at a mid-hour window start is my assumption; correct it if it is wrong.
- E1's schedule storage had a defect (the library adds datetimes to the slots in place), fixed by PR #773. E3's deploy check needs that fix deployed first.

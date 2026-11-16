# Room model: 2-minute timing, 10-minute learning and a warm-up delay

## Problem Statement

The room model ([ADR-0035](../adr/0035-room-forecast-and-comfort-plan-come-from-a-small-learned-thermal-model.md)) learns and stores in whole hours, and it assumes the room starts warming the moment the heating is asked for. Two things follow:

- **Timing is wrong.** Radiators take time to warm a room. The forecast line rises as soon as a slot starts, and the Comfort Plan's pre-heat is sized as if heat arrived at once, so the plan switches on too late.
- **The detail is too coarse.** One point and one bar an hour hide what a 45-minute slot does. The thermostat reports every 2 minutes, yet the plan can only be read to the hour on the charts.

## Solution

Each part of the model works at the grain its data supports ([ADR-0036](../adr/0036-room-model-times-at-two-minutes-learns-at-ten-and-has-a-warm-up-delay.md)):

- **Timing at 2 minutes**, the thermostat's reading interval. The forecast is run forward in 2-minute steps. The Comfort Plan switches on and off on 2-minute marks, for example on at 05:34 and off at 23:58. The Hive schedule is used exactly as programmed.
- **Learning at 10 minutes.** The Thermal Model's five numbers are learned from 10-minute room changes, and the forecast is stored and drawn every 10 minutes.
- **A Warm-Up Delay**, learned at each daily refit: the time between the thermostat calling for heat and the room responding. The forecast line stays flat for the delay after heating starts, the plan's pre-heat starts that much earlier, and the delay is shown as a figure on the dashboard. When it cannot be learned, the last learned value carries on.
- **Actual heating cost in half-hours**, the gas meter's grain, in place of hours.

Five dashboard panels get new SQL; four are unchanged. The daily gas Heating Model is not touched. Nothing is written to Hive.

Terms are in `.agent-docs/context.md`.

## User Stories

1. As the household, I want the actual heating cost drawn per half-hour, so that I see it at the finest grain the gas meter really measures.
2. As the household, I want the half-hourly history rebuilt from the thermostat readings on first run, so that the cost panels do not start empty.
3. As the household, I want the room forecast drawn every 10 minutes, so that I can see what a short slot does to the room.
4. As the household, I want the forecast to switch heating on and off at the minute my Hive schedule says, so that its minutes and cost match what the thermostat will do.
5. As the household, I want the Comfort Plan to switch on and off on 2-minute marks, so that its advice is as exact as the thermostat's own readings.
6. As the household, I want a Comfort Window time on an odd minute rounded earlier to a 2-minute mark, so that a config such as 07:45 keeps working.
7. As the household, I want the Thermal Model learned from 10-minute room changes, so that it captures what happens inside an hour without learning the thermostat's rounding.
8. As the household, I want the model still checked by the backtest and its 0.5 C gate, so that a poor model never reaches the dashboard.
9. As the household, I want the Warm-Up Delay learned from my own heating history, so that the model knows how long my radiators take.
10. As the household, I want the last learned delay to carry on when there is too little heating to learn one, so that the forecast does not lose it at the start of a season.
11. As the household, I want the forecast line to stay on its unheated course for the delay after heating starts, and heat already asked for to keep arriving for the delay after it stops, so that the line matches what the room does.
12. As the household, I want the Comfort Plan's pre-heat to allow for the delay, so that the room is at the Comfort Temperature when the window opens.
13. As the household, I want the delay shown as a figure, with whether it was learned, carried or not learned yet, so that I can see what was assessed and watch it change.
14. As the household, I want every panel that reads the forecast or the actual heating cost to keep working at the new grains, with an incomplete day shown as a gap, so that no panel shows a partial total.
15. As a maintainer, I want the new tables to accept rows of any length and the panel SQL to judge completeness by minutes covered, so that a later change of grain needs no schema or dashboard change.
16. As a maintainer, I want the guide, the queries document and the glossary to describe the new rules, and the retired tables listed for removal, so that the documents stay true.

## Implementation Decisions

**Modules changed** (octopus-app): the heating hours maths and its job, the thermal model maths and its job, the room forecast maths and its job, the comfort day job (a reader), the MariaDB client and table models, the config's comfort windows, the gas heating dashboard JSON and its queries document, and `docs/heating-model.md`. No hive-app change. The hourly job chain, its order and its job names (`heating_hour_refresh` and the rest) are unchanged, so the job health panel is untouched.

### Tables

Schema Sync creates the new tables and columns; nothing is renamed or dropped by the app.

```text
heating_interval         starts_at (naive UTC, key), ends_at, poll_count, on_minutes, measured_kwh,
                         heating_kwh, source ('measured' or 'estimated'), unit_rate (p/kWh),
                         cost_gbp, computed_at
room_forecast_interval   kind ('live' or 'day_ahead'), scenario ('current' or 'plan'),
                         starts_at (naive UTC)  -- the three are the key
                         ends_at, room_temp (at starts_at), on_minutes (one decimal),
                         heating_kwh, cost_gbp, computed_at
thermal_model            + step_minutes (10; NULL on rows fitted hourly, read as 60)
                         + warm_up_delay_minutes (NULL on old rows, read as 0)
                         + warm_up_delay_source ('learned', 'carried' or 'none')
                         + heating_starts
```

Each row carries its own start and end. The grain is one constant per writer: 30 minutes for `heating_interval`, 10 for `room_forecast_interval`. No reader may assume a grain; dashboard SQL sums `TIMESTAMPDIFF(MINUTE, starts_at, ends_at)`. `heating_hour` and `room_forecast_hour` stop being written. `room_forecast_slot` is unchanged (it already holds minute-exact starts and ends).

### Actual heating in half-hours

The rules of ADR-0033 with the hour replaced by the half-hour:

- A half-hour needs at least 12 of its 15 polls, or it has no row.
- Measured gas is the meter's one reading for that half-hour, used only when that London day's readings total above zero.
- The Hot-Water Baseline is per half-hour of the London day (48 of them), the median of heating-off half-hours in the last 28 days; a half-hour of day with under 5 such samples uses the median over all of them.
- The Effective Boiler kW is learned from measured half-hours with at least 10 minutes on, and needs at least 10 of them; otherwise the 12 kW default.
- The unit rate is the one in force at the interval's start.
- The job recomputes today and the three local days before it, and backfills a local day at a time from the newest stored interval, or on an empty table from the first poll's local day. That existing rule rebuilds the history on first run. Half-hours whose meter readings have passed the 45-day Retention Window come back as estimated.

Readers move to the new table: the comfort day job (a Heating Day and a Counted Hour are an hour containing an interval with minutes on) and the room forecast job (boiler kW).

### Room forecast in 2-minute steps

The forecast maths is rewritten as a simulation in 2-minute steps on even clock minutes. The parts-of-an-hour mechanism of `bugfix-room-forecast-sub-hour-slots` is removed: a 2-minute step has one target.

- **Start.** The step containing the newest reading; the room is the reading. The 30-minute limit on the reading's age stays.
- **One step.** With the model's numbers in per-hour units, a step of 2 minutes changes the room by `(2/60) * (loss_per_degree * (outdoor - room) + sun_gain * radiation + constant_gain) + heating_gain * arrived / 60 + carried_gain * (2 / step) * arrived_before / 60`, where `arrived` is the minutes of heat arriving in this step, `arrived_before` the minutes that arrived in the `step` minutes before it, and `step` is the model's `step_minutes` (60 for a model fitted hourly, so the forecast runs correctly on the existing model until the first 10-minute fit).
- **Weather.** Outdoor temperature is interpolated in a straight line between the hourly stamps; radiation is the mean of the hour the step falls in. An hour still needs its own stamp and the next, and the forecast stops at the first gap.
- **Demand and arrival.** Heat asked for at time `t` arrives at `t + delay`, where the delay is the model's Warm-Up Delay (0 when NULL). Inside a period with a target, the heat arriving in a step is the least that has the room at the target at the step's end, at most the whole step, and it is counted as demand (minutes, kWh, cost) in the step `delay` earlier. So nothing arrives during the first `delay` of a period, and heat keeps arriving for `delay` after the period ends, sized to that period's target. Heat demanded before the reading (the on polls of the last `delay + step` minutes) arrives as recorded.
- **Current schedule.** The Hive slot in force at each step's start. A slot starting on an odd minute takes effect from the 2-minute mark before it. Mode OFF demands nothing. MANUAL is still forecast as SCHEDULE.
- **Comfort Plan.** For each Comfort Window, the target is held from `opening - lead` to the closing. The lead is the shortest, in whole 2-minute steps, that has the room at the target at the opening; zero when it is already there, and from the forecast's start when the target is out of reach. Because heat arrives late, the lead includes the delay. Window times are rounded earlier to a 2-minute mark when the config is read, with a log line; a window whose start and end differ but round to the same mark is refused at startup, since it would otherwise be read as lasting until the next day.
- **Stored rows.** One per 10 minutes: the room at its start (the first row keeps its 10-minute start and holds the reading), the demanded minutes summed over its steps, kWh at the boiler kW and cost at the unit rate in force at its start. The gas unit rate is read once per distinct hour of a run, not once per row.
- **Day-ahead.** Tomorrow's rows are rewritten each run and freeze at midnight, written only when they cover every minute of tomorrow.
- **Slots.** The current slots are stored as today; the plan's slot starts at `opening - lead`, kept once it has passed.

Example (made-up schedule, delay 14 minutes): a slot at 19 C from 05:40 to 06:25. Gas is counted from 05:40. The room line follows its unheated course until 05:54, then climbs. Demand stops at 06:24, because the 7 C slot that starts at 06:25, an odd minute, takes effect from the 06:24 step; heat keeps arriving until 06:38. For the plan, if a 06:30 opening needs 42 minutes of arriving heat, the plan switches on at 05:34 (06:30 less 42 less 14).

### Thermal Model on 10-minute steps

The model is fitted from the thermostat polls directly (room temperature, state and boost in one read) and no longer reads stored heating rows.

- **A step** is 10 minutes on a 10-minute UTC mark. It needs: a room reading within 4 minutes after its start and after its end; at least 4 of the 5 polls in the span its heating is read from, and the same for the span before; outdoor temperature (interpolated) and radiation. A step missing any of these is left out.
- **Heating with a delay `d`.** The step's heating is the on-poll minutes in `[start - d, start + 10 - d)`; the carried heating is that of the 10 minutes before.
- **The fit** is the same least squares on the same five terms, with inputs scaled so the stored numbers stay per hour: the gap, radiation and constant are multiplied by 1/6, and heating enters as minutes over 60.
- **Usable** needs 1,008 steps (a week) and 30 heating steps (4 minutes on or more); the other checks and their order are unchanged.
- **Delay search.** Delays of 0, 2, 4 ... 40 minutes are tried on the steps that are complete at every delay; the delay with the smallest sum of squared misses wins, the smaller on a tie. It is stored as `learned` only when the 60-day window holds at least 10 Heating Starts (heating on after at least 30 minutes with none). Otherwise the newest stored delay is used and stored as `carried`; with none ever stored the delay is 0 and the source `none`.
- **Backtest.** Each of the last 14 local days is replayed in 10-minute steps by a model fitted on the 60 days before it, with that model's own delay (searched when its window has 10 starts, else the delay of the fit under test). A day is replayed only when all its steps are complete. The mean and worst miss, the 5-day minimum and the 0.5 C gate are unchanged.
- **Refit.** Once a local day, and also at once when the newest stored model is not a 10-minute fit, so the first run after deploy refits.
- **Cost.** 21 delays for the fit and for each backtest fit. The normal equations are built once per delay per window; the PR measures the refit on the Pi's data volume and it must finish within 5 minutes.

### Dashboard: all nine panels assessed

| Panel | Reads | Change |
| --- | --- | --- |
| Indoor, Target and Outdoor Temperature | forecast (query E) | E reads `room_forecast_interval` |
| Daily Gas and Outdoor Temperature | daily tables | none |
| Heating Cost per Hour | actuals | renamed **Heating Cost per Half-Hour**, reads `heating_interval` |
| Minutes Below Comfort | `comfort_day` | none |
| Daily Outdoor Temperature Band | weather, polls | none |
| Gas Billing Period Progress | `cost_forecast` | none |
| Heating Plan and Room Model | forecast, model | A and D read the new table; new query E, the delay figure |
| Schedule Today | slots, forecast | cost to midnight by minutes covered |
| Daily Cost: Schedules vs Actual | forecast, actuals | both tables, complete by minutes covered |

Temperatures, query E:

```sql
SELECT
  r.starts_at AS time,
  MAX(CASE WHEN r.scenario = 'current' THEN r.room_temp END) AS room_current,
  MAX(CASE WHEN r.scenario = 'plan' THEN r.room_temp END) AS room_plan
FROM room_forecast_interval r
WHERE r.kind = 'live'
  AND r.scenario IN ('current', 'plan')
  AND $__timeFilter(r.starts_at)
GROUP BY r.starts_at
ORDER BY r.starts_at;
```

Heating Cost per Half-Hour:

```sql
SELECT time, measured_cost_gbp, estimated_cost_gbp
FROM (
  SELECT
    h.starts_at AS time,
    IF(h.source = 'measured', ROUND(h.cost_gbp, 4), NULL) AS measured_cost_gbp,
    IF(h.source = 'estimated', ROUND(h.cost_gbp, 4), NULL) AS estimated_cost_gbp
  FROM heating_interval h
  WHERE $__timeFilter(h.starts_at)
    AND h.on_minutes > 0
    AND h.cost_gbp IS NOT NULL
) d
ORDER BY time;
```

Heating Plan and Room Model, query A (minutes are now out of 10 per bar):

```sql
SELECT
  r.starts_at AS time,
  MAX(CASE WHEN r.scenario = 'current' THEN r.on_minutes END) AS current_minutes,
  MAX(CASE WHEN r.scenario = 'plan' THEN r.on_minutes END) AS plan_minutes,
  MAX(CASE WHEN r.scenario = 'current' THEN ROUND(r.cost_gbp, 3) END) AS current_cost,
  MAX(CASE WHEN r.scenario = 'plan' THEN ROUND(r.cost_gbp, 3) END) AS plan_cost
FROM room_forecast_interval r
WHERE r.kind = 'live'
  AND r.scenario IN ('current', 'plan')
  AND $__timeFilter(r.starts_at)
GROUP BY r.starts_at
ORDER BY r.starts_at;
```

Query D (forecast age) changes only its table name. Queries B and C are unchanged. New query E, drawn like the backtest figures, with value mappings 0 "learned", 1 "carried", 2 "not learned yet":

```sql
SELECT
  LEAST(UTC_TIMESTAMP(), $__timeTo()) AS time,
  m.warm_up_delay_minutes AS warm_up_delay,
  CASE m.warm_up_delay_source WHEN 'learned' THEN 0 WHEN 'carried' THEN 1 ELSE 2 END AS warm_up_delay_state
FROM thermal_model m
ORDER BY m.fitted_at DESC, m.id DESC
LIMIT 1;
```

Schedule Today: the slot rows are unchanged. Each of the two cost cells becomes (shown for `current`; `plan` is the same with its scenario), where `<midnight>` is the existing expression `CONVERT_TZ(DATE_ADD(DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}')), INTERVAL 1 DAY), '${timezone}', 'UTC')`:

```sql
(SELECT CONCAT('Cost to midnight: £', FORMAT(SUM(r.cost_gbp), 2))
 FROM room_forecast_interval r
 WHERE r.kind = 'live' AND r.scenario = 'current'
   AND r.ends_at > UTC_TIMESTAMP()
   AND r.starts_at < <midnight>
   AND EXISTS (SELECT 1 FROM room_forecast_slot s WHERE s.scenario = 'current'
               AND s.day = DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}')))
 HAVING MIN(r.starts_at) <= UTC_TIMESTAMP()
    AND SUM(IF(r.cost_gbp IS NULL, 0, TIMESTAMPDIFF(MINUTE, r.starts_at, LEAST(r.ends_at, <midnight>))))
        = TIMESTAMPDIFF(MINUTE, MIN(r.starts_at), <midnight>))
```

The total runs from the start of the interval containing now to local midnight, and is blank unless priced rows cover every minute of it.

Daily Cost: Schedules vs Actual (the `bounds` CTE and the final SELECT are unchanged):

```sql
spans AS (
  SELECT 'current' AS series, r.starts_at, r.ends_at, r.cost_gbp
  FROM room_forecast_interval r CROSS JOIN bounds b
  WHERE r.kind = 'day_ahead' AND r.scenario = 'current'
    AND r.starts_at >= b.from_utc AND r.starts_at < b.to_utc
  UNION ALL
  SELECT 'plan' AS series, r.starts_at, r.ends_at, r.cost_gbp
  FROM room_forecast_interval r CROSS JOIN bounds b
  WHERE r.kind = 'day_ahead' AND r.scenario = 'plan'
    AND r.starts_at >= b.from_utc AND r.starts_at < b.to_utc
  UNION ALL
  SELECT 'actual' AS series, h.starts_at, h.ends_at, h.cost_gbp
  FROM heating_interval h CROSS JOIN bounds b
  WHERE h.starts_at >= b.from_utc AND h.starts_at < b.to_utc
),
daily AS (
  SELECT
    series,
    DATE(CONVERT_TZ(starts_at, 'UTC', '${timezone}')) AS day,
    SUM(cost_gbp) AS cost
  FROM spans
  GROUP BY series, DATE(CONVERT_TZ(starts_at, 'UTC', '${timezone}'))
  HAVING SUM(IF(cost_gbp IS NULL, 0, TIMESTAMPDIFF(MINUTE, starts_at, ends_at))) = TIMESTAMPDIFF(
    MINUTE,
    CONVERT_TZ(CAST(day AS DATETIME), '${timezone}', 'UTC'),
    CONVERT_TZ(CAST(day + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
  )
)
```

While the forecast still writes hourly rows (between the first and second slices), the two forecast branches read `room_forecast_hour` with `hour_start AS starts_at, hour_start + INTERVAL 1 HOUR AS ends_at`.

The queries document is updated with each query in the same PR as the JSON.

### Delivery

One PR with five commits, one per slice, in this order: actual heating in half-hours; the forecast in 2-minute steps stored every 10 minutes; the model learned on 10-minute steps; the Warm-Up Delay learned, used and shown; the guide and the retired tables. Each commit passes the tests on its own, so the branch can still be cut into separate PRs if review asks for it. Deploy octopus-app before importing a dashboard that reads a new table. After the dashboard review, `heating_hour` and `room_forecast_hour` are dropped by hand.

## Testing Decisions

Three existing seams, agreed with the household. Tests assert stored rows and returned values, not internals, and use a made-up schedule (a slot from 05:40 to 06:25 at 19 C), never the household's real slot times: this repository is public.

1. **Each job's `refresh(now)` on the test database** (the main seam): polls, weather and meter readings in, stored rows out. Prior art: `test_heating_hour_job.py`, `test_thermal_model_job.py`, `test_room_forecast_job*.py`, `test_hourly_heating_chain.py`.
2. **The pure maths** with hand-computed values, for the delay search and a single forecast step. Prior art: `test_room_forecast.py`. The delay search is tested on polls generated from a made-up house with a known delay, which the search must recover.
3. **The dashboard JSON checks** in `test_grafana_dashboards.py`: each changed panel reads the new tables, completeness is by minutes covered, and each query matches the queries document.

Panel SQL is not executed in tests. It is run once, read-only, on a throwaway copy of the database at the dashboard review, never on the live MariaDB.

Tests that encode the replaced rules (hourly rows, parts of an hour, the hourly fit and replay) are rewritten to the new rules, not kept beside them.

## Out of Scope

- The daily gas Heating Model and the Gas Cost Forecast.
- Writing anything to Hive, and Presence-Based Heating Control.
- Learning at 2 minutes, or storing the forecast every 2 minutes (the tables allow it later).
- Timing each heating run as a second measure of the delay.
- A delay that varies with outdoor temperature or flow temperature.
- MANUAL mode, and a boost running at forecast time (still ignored).
- Running the jobs more often than hourly.
- Carrying the few hourly day-ahead days over to the new table.
- hive-app, and the poll interval.

## Further Notes

- **The delay has not been measured.** The household chose to design without reading the Pi, so nobody knows yet whether it is 5 or 30 minutes. The first refit after the fourth PR is the assessment; the figure, its source and the number of Heating Starts are then recorded in the guide.
- **Risks to watch at the first refits.** The backtest needs every 10-minute step of a day, which is stricter than the hourly rule, so fewer days may qualify. Ten-minute changes are small against 0.1 C readings, so the numbers may be noisier than the hourly fit. Either can make the model unusable; the panel then says why, as today.
- **Thresholds are first values.** 12 of 15 polls, 30 heating steps, 10 Heating Starts, the 0 to 40 minute range: each is a named constant, to be revisited with real data.
- The stored `carried_gain` now refers to the previous 10 minutes, so it is not comparable with rows fitted hourly; the other four numbers are.
- The default weekend window opens at 07:45, which becomes 07:44.
- A delay at the top of its range (40 minutes) means the range is too short, as with the Heating Model's blend at its limit.
- If hive-app's poll interval changes, the 2-minute step and the polls-per-step thresholds change with it.

# Schedule table panel on the gas and heating dashboard

## Problem Statement

The household can see the room forecast as lines and bars, but not the plain answer to "when is the heating on today, and to what temperature?" for the Hive schedule as programmed and for the **Comfort Plan**. The Hive schedule is only visible in the Hive app, and the plan's pre-heat start is not stored anywhere: it exists only inside the hourly job's calculation. They also cannot see at a glance what each would cost for the rest of the day.

## Solution

One new table panel on the gas and heating dashboard with two columns, "Current schedule" and "Comfort plan", for today's local day (it swaps to the next day at 00:00 London time). Each row is a heating slot written `hh:mm - hh:mm | N degrees`. The current-schedule column lists every Hive slot of the day. The comfort-plan column is one slot, from when the plan switches heating on (including the pre-heat) to when the **Comfort Window** closes, at the Comfort Temperature. The last row of each column is the estimated gas cost from now to midnight.

Example with a made-up schedule:

```text
Current schedule              Comfort plan
00:00 - 05:40 | 7 degrees     05:10 - 00:00 | 19.5 degrees
05:40 - 06:25 | 19 degrees
06:25 - 00:00 | 7 degrees
Cost to midnight: £0.84       Cost to midnight: £1.12
```

This panel is an **exception to the house style "all panels are charts, no tables"** of [feature-gas-heating-dashboard-wave-1](feature-gas-heating-dashboard-wave-1.md). It is the only table; every other panel stays a chart or the one bar gauge. The household asked for it, so the rule is relaxed for this one panel, and the dashboard test names it explicitly.

## User Stories

1. As the household, I want a table of today's Hive schedule slots, each as start, end and target, so that I can see when the heating is told to run and to what temperature without opening the Hive app.
2. As the household, I want the comfort plan as one slot from when heating switches on (including the pre-heat) to when the Comfort Window closes, at the Comfort Temperature, so that I can compare it with the Hive schedule and see when the plan would start the boiler.
3. As the household, I want the table to show today and change to the next day at 00:00 London time, so that it always describes the day in progress.
4. As the household, I want a final row with each column's estimated cost from now to midnight, so that I can see what the rest of the day costs under each.
5. As the household, I want the plan's start to stay put once its pre-heat time has passed, so that the row neither vanishes nor jumps during the morning.
6. As the household, I want the plan to change through the day as the room and the weather forecast change, so that the table is the latest advice.
7. As the household, I want an empty column and no cost when the weekly schedule is unreadable or there is no comfort temperature, so that a wrong schedule is never shown.
8. As the household, I want the Hive slots still listed when Hive's mode is OFF, with a cost of zero, so that I can see what is programmed and that nothing will heat.
9. As a maintainer, I want the dashboard test to allow exactly one table panel and name it, so that the chart-only rule still catches an accidental second table.
10. As a maintainer, I want the slots stored by the hourly job and read by a plain SELECT, so that the panel does no JSON unpacking or model maths on the Pi (512 MB cap).

## Implementation Decisions

- **New table `room_forecast_slot`** (octopus-app `data/mysql/model.py`, created by Schema Sync, additive only, [ADR-0005](../adr/0005-additive-only-schema-sync.md)). One row per local day, scenario (`current` or `plan`) and slot: the local day, the scenario, a slot index (order within the day), the slot's start and end as naive UTC (so DST days are exact; the panel formats them in London time), the target in degrees, and `computed_at`. Naming follows `room_forecast_hour`. Existing tables are untouched.
- **Rows written by `RoomForecastJob.refresh`** (octopus-app `data/room_forecast_job.py`), for today and tomorrow, both scenarios, every run, replacing every stored row of that scenario (so past days drop away and the table stays tiny) through a new `MariaDBClient.replace_room_forecast_slots`, as `replace_room_forecast` does for hour rows. It lives beside the hourly forecast and uses the same inputs (the latest weekly schedule, the thermal model, the comfort temperature).
- **Current-schedule slots** come from the parsed weekly schedule (`room_forecast.parse_schedule`), by local day: each slot runs from its start to the next slot's start, the day's first slot is not taken from midnight but from the previous day's last slot target until the first start (the same carry-over `target_at` uses), and the last slot ends at 00:00. A slot whose target repeats the one before it is merged into it, as `schedule_parts` treats a repeated target as no change. An unreadable or missing schedule stores no current rows and removes earlier ones, with the same WARNING as the hour rows. Mode OFF does not change the slots (the schedule is what is programmed).
- **Comfort-plan slots**: `room_forecast.comfort_plan` already finds, per day, the held span `(window opening minus pre-heat lead, window closing)`. It is exposed to the job as the plan's slots (one per window, stored under the local day the window OPENS on, position 0: start, end, target is the Comfort Temperature rounded up to a half degree). No comfort temperature stores no plan rows and removes earlier ones, as today.
- **Keeping the plan's start once begun.** When the stored slot for a day has a start at or before the run's time, a later run keeps that start and replaces only the end and target. A day with no stored slot, or whose stored start is still ahead, takes the new calculation. This is the only state kept between runs.
- **Dashboard** (`data/grafana/gas-heating-dashboard.json`, new panel after panel 7, a Grafana `table` panel): a SQL query that selects the local day's rows (today in the `timezone` variable, using the existing `$__timeTo()`-independent form: `UTC_TIMESTAMP()` converted with `CONVERT_TZ` to `${timezone}`, as the other panels do), one text column per scenario, rows paired by slot index, plus a final cost row. The cost is the sum of `room_forecast_hour.cost_gbp` for `kind = 'live'` of each scenario for hours from now to the next local midnight. Column titles "Current schedule" and "Comfort plan". Queries respect the data source Database setting and stay small (a day's rows). The text of each cell is built in SQL (`DATE_FORMAT` of the local times and the target), because the format is the product.
- **Spec and tests that state the old rule.** The wave-1 spec line "All panels are charts, no tables" gets a pointer to this spec. `test_grafana_dashboards.py`'s chart-only test is reworded to allow exactly the types `timeseries`, `bargauge` and one `table`, the schedule table.
- **Docs.** The heating guide (`docs/heating-model.md`) gets a short section on the table: what each column means, that the plan's start is kept once begun, and that cost is from now to midnight. `context.md` gains no new term (Comfort Plan, Comfort Window and Comfort Temperature already exist); a **Slot** is Hive's own word and is used as in the existing schedule code.
- **The public repo.** Specs, tests and the PR use a made-up schedule (a slot 05:40-06:25 at 19 C), never the household's real Hive times.

## Testing Decisions

A good test asserts what is stored and what the dashboard file contains, not internals.

1. **The job seam**: `RoomForecastJob.refresh` against the SQLite `mariadb_client` fixture, as `tests/test_room_forecast_job_slots.py` does for the hour rows, with the shared fixtures in `tests/room_forecast_fixtures.py`. Seed a usable thermal model, a thermostat reading, a made-up weekly schedule, a weather forecast and a comfort day; run with a fixed clock; assert the stored `room_forecast_slot` rows for both scenarios, for today and tomorrow. The slot maths is covered through this seam, with no new unit-test file.
2. **The dashboard file test** in `tests/test_grafana_dashboards.py`: the panel exists, it is the only `table`, it reads `room_forecast_slot`, its query filters to the local day, and its targets name the `home_monitoring` dataset like every other panel.

Prior art: `test_room_forecast_job_slots.py`, `test_room_forecast_job_first_hour.py`, `test_grafana_dashboards.py`.

Behaviours to cover: the slot rows of a day with several slots (merged repeats, previous-day carry-over for the first slot, last slot ending at 00:00); the plan's single slot with its pre-heat start; the plan's start kept after its time has passed while the end and target update; an unreadable schedule leaving no current rows and removing the old ones; no comfort temperature leaving no plan rows and removing the old ones; mode OFF still storing the Hive slots; a DST day.

## Out of Scope

- Tomorrow in the table (the job stores tomorrow's slots, so a later change is a query change).
- The separate panel comparing the estimated cost of the schedule, the plan and the actual, which the household will specify later.
- The cost of the elapsed part of the day.
- Writing anything to Hive.
- Importing the dashboard JSON to Grafana and any Grafana check: both happen in the single end-of-plan dashboard review.
- Running the new panel SQL against the live Pi database.

## Further Notes

- The table is empty on the first day after deploy until the first run has written rows; that is not a failure.
- Deploy order: deploy octopus-app (Schema Sync creates the table) before importing the dashboard that reads it.
- The "MANUAL mode is forecast as if it were SCHEDULE" limit of the room forecast carries into the slots: a manually held temperature is not shown.
- A plan slot shows on the day its window opens. With a non-default overnight window (closing after midnight) the slot still shows under yesterday while it is open past midnight, so today's plan column is blank until the new day's window starts. The default windows close at midnight, so this is not hit today.
- The cost row sums the live hours from the current hour to the next local midnight (the live forecast starts at the newest reading's hour, so the current hour counts in full).

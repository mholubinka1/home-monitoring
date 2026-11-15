# Daily cost comparison panel on the gas and heating dashboard

## Problem Statement

The household can see what the heating will cost for the rest of today under the Hive schedule and under the **Comfort Plan** (the schedule table's "Cost to midnight" row), and what each hour of heating actually cost (Heating Cost per Hour). They cannot see, day by day over the past month, whether the comfort plan would have cost less than the schedule they actually run, nor how both compare with what the heating really cost.

## Solution

One new time series panel on the gas and heating dashboard with three lines of daily gas heating cost in pounds over the last 30 completed London days (yesterday back to 30 days ago; today is never shown):

1. **Current Schedule (estimated)**: what the Hive schedule as programmed was forecast to cost that day.
2. **Comfort Schedule (estimated)**: what the Comfort Plan was forecast to cost that day.
3. **Actual**: what the heating really cost that day.

The two estimates come from each day's **Day-Ahead Forecast**, which the hourly job rewrites on every run until midnight and so freezes at the last run before the day begins; each is the forecast the household actually had when the day began, not a hindsight replay. All three lines are heating-only gas cost at the gas unit rate with no standing charge, so the gaps between them are purely about the schedule and what happened. A day is drawn only when every hour of it is present and priced; otherwise that day is a gap, never a partial total and never a zero. The estimated lines start at the first Day-Ahead Forecast rows (about 2026-11-16) and fill up one day at a time; nothing is backfilled.

## User Stories

1. As the household, I want a Current Schedule (estimated) line of daily cost from the day-ahead forecast of the Hive schedule, so that I can see what my programmed schedule was expected to cost each day.
2. As the household, I want a Comfort Schedule (estimated) line of daily cost from the day-ahead forecast of the Comfort Plan, so that I can see what the plan would have cost on the same days.
3. As the household, I want an Actual line of daily heating cost on the same basis as the estimates (heating-hour cost, unit rate, no standing charge), so that the three lines are comparable and the gaps between them mean something.
4. As the household, I want the panel to show the previous 30 completed London days and never the current day, so that no line is drawn from a day that is still in progress.
5. As the household, I want the 30-day window to stay fixed whatever the dashboard time range is, so that changing the picker does not change what this panel compares.
6. As the household, I want a day with any missing or unpriced hour to be a gap in that line, so that a data outage never looks like a cheap day.
7. As the household, I want days before the first day-ahead forecast to leave the two estimated lines empty (with the Actual line still drawn), so that nothing invented is shown as a forecast the household never had.
8. As the household, I want the panel's description to say what each line is, that costs are heating-only at the unit rate with no standing charge, and why the estimated lines start late, so that the panel can be understood without opening the spec.
9. As a maintainer, I want the panel to read only the small `room_forecast_hour` and `heating_hour` tables, so that it does no heavy work on the Pi (512 MB cap).
10. As a maintainer, I want the panel appended below the schedule table, so that no existing panel moves and the grid-tiling test keeps passing.
11. As a maintainer, I want the queries doc and dashboard tests to describe and pin the panel, so that its rules (window, complete-day test, scenarios, tables) are not silently changed.

## Implementation Decisions

**Modules changed** (no application, job, schema or Hive change; octopus-app does not need a redeploy for this panel, because the tables and rows it reads already exist):

- `data/grafana/gas-heating-dashboard.json`: new panel with id 9, type timeseries, titled "Daily Cost: Schedules vs Actual", full width (24 columns) and 9 rows high, at `x = 0`, `y = 61` (directly below the Schedule Today table, which ends at row 61). Same data source, `dataset` and `editorMode` as the other panels.
- `data/grafana/mariadb/gas-heating-queries.md`: a new section for the panel, with its SQL, the rules below and the usual "not yet checked against the Pi" note.
- `.agent-docs/context.md`: the **Gas Heating Dashboard** entry's panel summary gains the new panel (the existing sentence lists what the dashboard has gained since the review). No new glossary term.
- `apps/octopus-app/tests/test_grafana_dashboards.py`: tests for the panel (see Testing Decisions).

**The three series.** One query, three columns, one row per London day: `time` (London noon of the day expressed in UTC, `CONVERT_TZ(CAST(day AS DATETIME) + INTERVAL 12 HOUR, '${timezone}', 'UTC')`, as the daily forecast outdoor line stamps its days; noon rather than midnight so that the oldest day's point is never earlier than the start of the panel's time range), `current_cost`, `plan_cost` and `actual_cost`, each rounded to 4 places. Series are named by column and given display names through overrides: "Current Schedule (estimated)", "Comfort Schedule (estimated)" and "Actual". The estimated lines are dashed and the Actual line solid, so the forecast reads as a forecast; each has its own fixed colour, none shared with another series on the dashboard that means something different. Unit is pounds (`currencyGBP`), two decimals, `spanNulls` off so a gap is a visible break and not a joined line. A day that has no complete hours in any series returns no row at all, so `insertNulls` is set to 26 hours (in milliseconds) to break the line across a missing day (neighbouring days' points are 23 to 25 hours apart, a missing day makes it 47 to 49).

**Sources.**

- *Current Schedule (estimated)* is the sum of `room_forecast_hour.cost_gbp` where `kind = 'day_ahead'` and `scenario = 'current'`; *Comfort Schedule (estimated)* is the same with `scenario = 'plan'`. The estimates come only from the `day_ahead` rows (frozen at the last run before the day begins), never from `live` rows, which are replaced every hour and so are not what the household had at the start of the day.
- *Actual* is the sum of `heating_hour.cost_gbp` of both sources (measured and estimated), so a day whose gas has not yet arrived is still drawn from the minutes-on estimate, as in the Heating Cost per Hour panel. Hours with the heating off (on-minutes 0) are rows with a cost of zero and count towards the day's hours.
- Each hour is assigned to a London day by converting its UTC `hour_start` with `CONVERT_TZ(hour_start, 'UTC', '${timezone}')` (ADR-0010).

**The complete-day rule.** A series has a value for a London day only when its count of non-NULL `cost_gbp` rows equals the number of hours in that day, `TIMESTAMPDIFF(HOUR, <day start in UTC>, <next day start in UTC>)` (23, 24 or 25 across clock changes), the same shape the daily outdoor and schedule-table cost rules use. This covers a missing hour (too few Hive readings for `heating_hour`, a forecast run cut short) and an unpriced hour (NULL `cost_gbp`, no gas rate) with one test. It is applied per series, so one line can be a gap while another is drawn.

**The window.** Fixed in the query, not taken from the dashboard range: London days from today minus 30 up to yesterday inclusive, where today is `DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}'))`. Today is excluded by the upper bound, so it can never appear even if the panel range reaches into it. The panel carries a relative time override of 31 days (`timeFrom: "31d"`) so that its axis spans the window whatever the dashboard's range (14 days back, 7 forward) is: the oldest day's noon is always less than 31 days before now, so its point is on the axis at any time of day. The query's own bounds are the authority on which days have points, so it does not use `$__timeFilter`.

**Performance.** The query touches only `room_forecast_hour` (at most about 2 x 24 x 30 day-ahead rows, plus a couple of days of live rows) and `heating_hour` (about 24 rows a day), filtered to the 31-day window. No `weather_observation`, `heating_status`, `daily_consumption_summary` or `consumption` access.

**Deploy.** Import the dashboard. No app deploy is needed. Nothing is checked on the live MariaDB (a 512 MB cap, OOM on 2026-11-05); the SQL is checked once, read-only, on a throwaway copy of the database in the final dashboard review, as the other new panels are.

## Testing Decisions

Good tests assert what the dashboard JSON and its SQL say, not how they were written. The one seam is the dashboard test module, which reads `gas-heating-dashboard.json` and asserts on each panel's type, layout, overrides and SQL text. The SQL cannot run on the SQLite test database (it uses `CONVERT_TZ` and MariaDB date functions), so its behaviour is checked on a throwaway MariaDB copy in the final dashboard review, not here.

Prior art, in `apps/octopus-app/tests/test_grafana_dashboards.py`: `test_the_plan_panel_compares_the_two_schedules_in_minutes_and_pounds`, `test_the_plan_panel_reads_only_the_small_forecast_and_model_tables`, `test_the_schedule_table_ends_with_each_scenarios_cost_to_midnight` and `test_the_panels_tile_the_grid_without_gaps_or_overlaps`.

Scenarios, each a red-green step:

1. The panel exists with a unique id, is a timeseries, is full width, and sits below the schedule table; the tiling test still passes.
2. The query returns three named series, `current_cost`, `plan_cost` and `actual_cost`, with the display names "Current Schedule (estimated)", "Comfort Schedule (estimated)" and "Actual", in pounds, dashed for the two estimates and solid for Actual, not joined across gaps.
3. The estimates read `kind = 'day_ahead'` rows of the `current` and `plan` scenarios and never `live`; Actual reads `heating_hour` of both sources.
4. The window is 30 London days ending yesterday: the query bounds exclude the current day, and the panel has a 31-day relative time override.
5. A day is kept per series only when its count of priced hours equals the hours in the London day (one `HAVING COUNT(...) = TIMESTAMPDIFF(HOUR` rule, applied per series because the rows are grouped by series first).
6. The panel reads only `room_forecast_hour` and `heating_hour`.
7. The dashboard-wide tests (chart-only types, distinct panel ids, every variable used) still pass.

## Out of Scope

- Any change to the room forecast job, `heating_hour` job, schema, or Hive.
- Backfilling day-ahead estimates for days before the first stored row.
- Octopus's total gas bill (consumption x rate plus standing charge) as a fourth line or as the Actual line.
- Savings arithmetic (the difference between lines), totals or summary stats for the 30 days.
- A day-ahead forecast of room temperature against the measured room (a separate follow-up noted in the E3 spec).

## Further Notes

- The Actual line is not independent of the estimates' scale: its measured hours subtract a learned hot-water baseline and its estimated hours use the learned boiler kW, the same basis the forecast uses, which is why it is the like-for-like comparison.
- `heating_hour` has no row for an hour with too few Hive readings, so one sparse hour makes that whole day a gap in the Actual line. This is intended (decision above), and a long gap in Actual means look at the hive-app health.
- A day-ahead row is written only while its day is still tomorrow and only when the run covered every hour of that day, so a day with no day-ahead row is a gap in both estimated lines.

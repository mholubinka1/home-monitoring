# Estimate heating intervals with a warm-up and a steady kW

## Problem Statement

The Gas Heating Dashboard's "Heating Cost per Half-Hour" panel shows each heating half-hour's cost. A half-hour is measured once Octopus's half-hourly gas arrives (one to two days later); until then it is estimated as its minutes on times one Effective Boiler kW. The household sees the estimated bars sitting consistently and clearly below the measured ones.

The boiler does not draw steadily through a Heating Run. At the start the radiator water is cold and the burner runs near full rate; once the water is hot the boiler turns itself down. On 2026-10-21 to 2026-11-16 (81 measured heating half-hours, 42 runs) the single kW (11.4) was right in total (estimate ÷ actual 0.98) but:

| Half-hour | Actual kW per hour on | Estimate ÷ actual |
| --- | --- | --- |
| The one a Heating Run starts in | 15.3 | 0.76 |
| Later ones in the run | 8.7 | 1.35 |

The tall bars are run starts, so the estimate reads about a quarter low exactly where the eye looks. When the gas arrives the estimate is overwritten, so nobody can compare estimate and actual for the same half-hour and the model's accuracy cannot be checked.

## Solution

Each estimated Heating Interval is priced from two learned figures: its minutes in the first 20 minutes of a Heating Run at the **Warm-up kW**, and its later minutes at the **Steady kW** ([ADR-0039](../adr/0039-heating-intervals-are-estimated-with-a-warm-up-and-a-steady-kw-per-heating-run.md)). In a leave-one-day-out backtest on the household's data this took the mean miss per half-hour from 1.37 to 0.79 kWh and removed both biases (run start 0.99, later 0.97); the figures came out at about 17 and 6 kW. With too little evidence the panel keeps using the single Effective Boiler kW, as today.

Every half-hour also keeps its **Heating Estimate**, frozen at its last value once the measured gas arrives, so the estimate and the actual gas sit side by side in the table.

Measured half-hours, the hot-water baseline, the room forecast and the "Daily Cost: Schedules vs Actual" panel are unchanged.

## User Stories

1. As a member of the household, I want the estimated half-hours in "Heating Cost per Half-Hour" priced higher when a Heating Run starts and lower once it has been running, so that the estimated bars look like the measured ones they turn into.
2. As a member of the household, I want the Warm-up and Steady kW learned from our own last 28 days of measured half-hours, so that they follow our boiler and radiators rather than a guess.
3. As a member of the household, I want estimates to fall back to today's single kW while there are fewer than 10 measured heating half-hours to learn from, or when either learned figure is not positive, so that estimates never vanish or turn nonsensical.
4. As a member of the household, I want a Heating Run to carry on through a single missed or OFF poll, so that a thermostat flicker is not priced as a fresh warm-up.
5. As a member of the household, I want a run that started before the job's recompute window to keep counting from its real start, so that the first half-hour of the window is not wrongly priced as a warm-up.
6. As the person tuning the model, I want every half-hour to store its Heating Estimate, so that I can compare estimate and actual with a simple query.
7. As the person tuning the model, I want a measured half-hour to keep the last estimate made before its gas arrived, so that the comparison is honest rather than learned from the answer.
8. As the person tuning the model, I want a half-hour that is measured from its first computation (a backfill) to store the estimate of that run's figures, so that no row is left without an estimate going forward.
9. As a member of the household, I want the two-phase estimate to work from the first run after the deploy, so that I do not wait weeks for new rows to build up.
10. As a member of the household, I want the rows stored before the deploy to keep their measured kWh and cost exactly as they were, so that the history does not shift.
11. As a member of the household, I want panel 3's description to say how estimates are made now, so that I know what an estimated bar means.
12. As a member of the household, I want the room forecast and the "Daily Cost: Schedules vs Actual" panel unchanged by this change, so that it can be judged on panel 3 alone first.

## Implementation Decisions

- **heating_intervals module** (the pure half-hour helpers):
  - A named constant for the warm-up length, 20 minutes, and one for the longest gap between ON polls inside one Heating Run, 5 minutes: one missed or OFF poll at the 2-minute poll interval makes a gap of about 4 minutes, two make about 6, and polls drift by seconds, so the limit sits between them.
  - `heating_on_minutes` also returns each half-hour's early on-minutes: the minutes of its ON polls that fall less than 20 minutes after their Heating Run's first ON poll, two minutes per poll, never above the half-hour's on-minutes. Runs are found across half-hour boundaries from the poll sequence. Polls before the window that only serve to locate a run's start produce no half-hour.
  - A new learning function takes measured (on-minutes, early on-minutes, heating kWh) half-hours with any minutes on and returns the Warm-up and Steady kW by two-unknown least squares with no intercept, or nothing when there are fewer than 10 such half-hours, the system has no unique solution, or either figure is zero or negative.
  - The estimate function prices early minutes at the Warm-up kW and the rest at the Steady kW; when given only a single kW it behaves as today.
  - `learned_boiler_kw` stays as it is: it is the fallback here and still prices the room forecast.
- **HeatingIntervalJob**:
  - Reads polls from 25 minutes (warm-up plus the longest gap) before each recompute window, so a run that started earlier is split correctly.
  - Learns the Warm-up and Steady kW from the same rows the single kW is learned from today (the measured rows of the 28 days before the window plus the window's own measured rows), using only rows that have early on-minutes. Falls back to the single kW when learning returns nothing.
  - Sets each row's Heating Estimate. An estimated row's Heating Estimate equals its heating kWh. A measured row keeps the Heating Estimate of the stored row it replaces, when that row had one; otherwise it gets the estimate from this run's figures.
  - **One-time fill:** at the start of a refresh, when stored rows in the 28 days before the window have no early on-minutes, it reads the polls of that span once (from 25 minutes before the first such row), works out each row's early on-minutes and writes only that column. Measured kWh, heating kWh, cost and source are not touched, and the Heating Estimate of those rows stays empty. Once every row has the split, the check finds nothing and costs one read of stored rows.
  - Logs the learned Warm-up and Steady kW (or that it fell back to the single kW) with each recompute.
- **Data model**: `HeatingIntervalRecord` gains `early_on_minutes: int | None` and `estimated_heating_kwh: float | None`.
- **Schema**: the `heating_interval` table gains two nullable columns, `early_on_minutes` (integer) and `estimated_heating_kwh` (double), added on startup by the existing additive schema sync. No index changes. The column name avoids "estimated kWh", which the glossary already uses for `est_kwh`.
- **MariaDBClient**: a method that writes early on-minutes onto existing `heating_interval` rows by start, used only by the one-time fill. `read_heating_intervals` and `replace_heating_intervals` carry the new fields through the record unchanged.
- **Grafana dashboard JSON** (gas heating): panel 3 "Heating Cost per Half-Hour" description says estimated half-hours use a learned warm-up kW for the first 20 minutes of each heating run and a steady kW after, falling back to a single learned kW with too little evidence. Its SQL is unchanged.
- **Performance**: each run reads 24 more minutes of polls (12 rows). The one-time fill reads up to 28 days of polls (about 20,000 rows of three columns) once. The least-squares fit is a 2×2 system over at most a few hundred rows. Nothing new runs on Grafana's side.

## Testing Decisions

- Tests go through `HeatingIntervalJob.refresh` on the `mariadb_client` fixture (a real `MariaDBClient` over an in-memory database): seed thermostat polls, gas readings and the gas rate, run the job at a fixed time, and assert on the stored `heating_interval` rows (heating kWh, cost, early on-minutes, Heating Estimate). No mocks of code we control, and no unit tests on the heating_intervals helpers. A good test states the household-visible outcome, for example "a half-hour holding a run's first 20 minutes is estimated at the Warm-up kW".
- Learning is tested with seeded measured history whose gas is generated from known Warm-up and Steady kW, so the estimate of a new half-hour can be checked against hand-worked numbers. Fallback cases: under 10 measured heating half-hours, history that implies a non-positive figure, history where every half-hour has the same split (no unique solution).
- Run cases: a run crossing a half-hour boundary; a single OFF poll inside a run; two OFF polls ending it; a run that started before the recompute window.
- Heating Estimate cases: an estimated row; a row that turns measured keeps its earlier estimate; a backfilled row measured from the start gets one.
- One-time fill: rows stored without early on-minutes get them from polls, with measured kWh, heating kWh, cost and computed_at unchanged.
- The existing tests that expect the single kW keep passing where they have under 10 measured half-hours with a split. Tests whose history now teaches two figures are updated to the two-phase outcome.
- The panel 3 description is checked in the existing Grafana dashboard JSON test (prior art: the `"forecast" in description` checks there).
- Prior art: `tests/test_heating_interval_job.py` (seed helpers `_seed_polls`, `_seed_gas`, `_record_heating_intervals`; the learned-kW and backfill tests) and `tests/test_grafana_dashboards.py`.

## Out of Scope

- The room forecast's costs (`room_forecast_interval`, panel 9 "Daily Cost: Schedules vs Actual", the schedule table). They keep the single Effective Boiler kW; moving them onto Warm-up and Steady kW is a follow-up change.
- Learning the warm-up length, a decaying power curve, and scaling the warm-up by how long the heating was off (ADR-0039 says why).
- The hot-water baseline (its median is 0 kWh while the mean of heating-off half-hours is about 0.2 kWh).
- A dashboard series or panel for the Heating Estimate.
- Filling the Heating Estimate of rows stored before the deploy.

## Further Notes

- The repo is public: tests, issues and PRs use made-up times (for example a run from 05:40), never the household's Hive schedule.
- After deploy, the octopus-app container updates at Watchtower's 03:00 UTC run unless recreated by hand. Check the first refresh's log line for the learned figures and that no rows lost measured values. A dashboard import is needed only for the description text.
- The backtest behind the numbers (leave-one-day-out over 18 days) was run on a copy of the live rows during the investigation; it is not part of the repo.

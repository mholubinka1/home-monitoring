# Hourly heating cost

## Problem Statement

The gas dashboard's third panel, "Boost Minutes and Estimated Extra Gas Cost", prices only the minutes a boost was active. Boosts are a small part of the gas the heating uses, so the panel misses nearly all heating gas. It also sums by day, so it cannot show which hours of the day the money went. The boiler rating behind it is a typed text box (`boiler_kw`, default 12) that came from one test evening, and the meter's gas includes hot water (a combi boiler's one burner serves both), which nothing separates out.

The household wants to see what the heating costs hour by hour, however it came on (schedule, manual raise or boost), with hot water kept out of the figure and a clear sign of which hours are measured and which are estimated.

## Solution

A new `heating_hour` table, written by an hourly octopus-app job, holds one row per completed UTC hour. Each row has the minutes the heating was on (a **Heating Hour** in the glossary), the heating kWh, the gas unit rate and the cost, labelled measured or estimated.

- Heating kWh is the measured half-hourly gas minus a learned **Hot-Water Baseline**, where the meter data exists. Otherwise it is the on-minutes times a learned **Effective Boiler kW**.
- The panel becomes an hourly stacked bar chart of heating cost in pounds, with a solid "measured" series and a lighter "estimated" series. An hour with too few Hive polls is a gap, never a zero.
- The boost-minutes bars, the boost cost query and the `boiler_kw` variable are deleted.

Decisions are in [ADR-0033](../adr/0033-hourly-heating-cost-learns-a-hot-water-baseline-and-the-boilers-effective-kw.md). Terms are in `.agent-docs/context.md`.

## User Stories

1. As the household, I want the dashboard to show what the heating cost in each hour, so that I can see when in the day the money goes.
2. As the household, I want every hour the heating was on to be priced (schedule, manual raise or boost), not just boosts, so that the panel reflects the whole heating bill.
3. As the household, I want hot water kept out of the heating figure, so that a shower in a heating hour is not charged to the heating.
4. As the household, I want hours with metered gas shown apart from hours that are only estimated, so that I know which bars to trust.
5. As the household, I want an hour estimated from the heating-on minutes until Octopus's gas arrives (about 1.5 days late), and then replaced by the measured figure, so that recent heating shows straight away and settles later.
6. As the household, I want the boiler's gas draw learned from my own measured hours instead of typed in, so that estimates follow what the boiler really does.
7. As the household, I want an hour with missing Hive polls to show as a gap, so that a Hive outage never looks like free heating.
8. As the household, I want each hour priced at the gas unit rate in force at that hour, with no standing charge, so that the cost is what the heating caused and a tariff change is followed.
9. As the household, I want the measured hourly figures kept after Octopus's raw half-hourly rows are pruned at 45 days, so that old measured hours do not vanish.
10. As the household, I want the old boost panel and its `boiler_kw` variable removed, so that the dashboard has no panel or control left that duplicates the new one.
11. As a maintainer, I want the decision, glossary, queries document and explainer updated with the code, so that the model can be understood and challenged later.

## Implementation Decisions

**Schema.** A new SQLAlchemy model `heating_hour` in octopus-app's `data/mysql/model.py`, added by Schema Sync (additive, ADR-0005). It is never pruned (about 8,800 rows a year). Columns:

- `hour_start` (DateTime, UTC, primary key)
- `poll_count` (Integer): Hive polls received in the hour
- `on_minutes` (Integer): `2 x` the polls where `state='ON' OR boost_active=1`
- `measured_kwh` (nullable): metered gas for the hour, when it exists
- `heating_kwh` (nullable): the figure priced
- `source` (String): `measured` or `estimated`
- `unit_rate` (nullable, pence per kWh): the gas rate in force at `hour_start`
- `cost_gbp` (nullable): `heating_kwh x unit_rate / 100`
- `computed_at`

An hour with no rate in force gets a NULL rate and cost (the hour is kept).

**Hive mirror.** The `MariaDBClient` in `data/mysql/client.py` gains a hand-mirrored Core `Table` for hive-app's `heating_status`, on the same unregistered `weather_metadata` pattern and kept in sync with hive-app's model by hand. A read method returns `(polled_at, state, boost_active)` rows for a UTC range. A missing table (hive-app never run) is treated as no polls, using the existing `_is_missing_table_error`.

**Hourly aggregation (pure functions in a new `data/heating_hours.py`).**

- Polls are bucketed by UTC hour. An hour is written only once it has ended.
- An hour with fewer than 24 polls (80% of 30) is left out: no row is written, and an existing row for that hour is removed. This keeps a Hive outage a gap.
- Measured gas for an hour is the sum of its two half-hourly `est_kwh` readings, used only when both exist and the readings present for that London day total above zero. A day of all zeros is the upstream "missing" marker (see Complete Gas Day) and is never taken as zero use.
- An hour with zero on-minutes and measured gas is a hot-water hour: `heating_kwh` is 0 and its `measured_kwh` feeds the baseline.

**Learned numbers (same module).** Both are recomputed from stored `heating_hour` rows each run, over the trailing 28 days, never from the Grafana-typed value.

- *Hot-Water Baseline* per London hour of day: the median `measured_kwh` of measured hours with zero on-minutes. An hour of day with fewer than 5 such hours uses the median over all hours of day; with none at all the baseline is 0.
- *Effective Boiler kW*: `sum(heating_kwh) / sum(on_minutes / 60)` over measured hours with 20 or more on-minutes. Below 5 such hours it falls back to 12.
- A measured heating hour's `heating_kwh` is `max(0, measured_kwh - baseline)` for its hour of day, and never exceeds `measured_kwh`.
- An estimated hour's `heating_kwh` is `on_minutes / 60 x effective kW`.

**Job.** A new `HeatingHourJob` in `data/heating_hour_job.py`, beside `HeatingModelJob` and in the same style (a class with `refresh(now)` taking the clock). Each run recomputes the hours that have ended in the last 4 London days, which is enough for late-arriving gas to turn an estimated hour into a measured one. When the newest stored hour is older than those 4 days, the run first backfills chronologically, one London day at a time, from the day after that hour (or from the earliest `heating_status` poll when the table is empty), so each day's learned numbers use the days written before it. This also resumes a backfill that died part-way and covers the job having been stopped for more than 4 days. A day is written in one transaction, so the newest stored hour's day is complete and is never rewritten by the backfill. Older hours with no gas stay estimated at whatever kW was learned at the time. Rows older than the 4-day window are never recomputed.

**Rates.** Each hour is priced from the gas agreement and `product_rate` rows in force at `hour_start`, through the existing client rate read (the one `CostForecastRetriever` uses), so a mid-period tariff change is followed.

**Scheduling.** A `register_heating_hour_job` in `main.py` on an hourly schedule, using the existing `_schedule_refresh_job` and its retry-with-backoff and `job_run` recording, with a new job name. It is a separate job from the daily 04:00 chain, because that chain sequences the heating model and the pruner and this one must run every hour.

**Dashboard (`data/grafana/gas-heating-dashboard.json`).** Panel 3 is replaced by "Heating Cost per Hour": stacked bars in pounds, two series (measured, solid; estimated, lighter) read from `heating_hour`, over the dashboard's existing 14 days back. Nothing is shown in the forward 7 days. The `boiler_kw` variable and the boost-minutes and boost-cost queries are removed from the JSON. The dashboard is imported, so deployment order is the app first, then the import; the live Grafana copy may have been edited, so re-export before editing the JSON.

**Docs.** The queries document `data/grafana/mariadb/gas-heating-queries.md` drops the `boiler_kw` variable and the Boost Minutes section, adds `heating_hour` to the schema block and a "Heating Cost per Hour" section with its query. `docs/heating-model.md` gets a plain-language section on hourly heating cost. ADR-0033 and the glossary terms (Heating Hour, Hot-Water Baseline, Effective Boiler kW) are already written. The hourly figures sit beside, not inside, the **Heating Model** (the daily weather fit), and `context.md`'s dashboard entry is updated to say panel 3 is hourly heating cost.

## Testing Decisions

One seam, the highest: `HeatingHourJob.refresh(now)` run against the existing SQLite `mariadb_client` fixture, as `tests/test_heating_job.py` does for the heating model. Tests seed `heating_status` polls (through the mirrored table), half-hourly gas, agreements and rates, run the job with a fixed clock, and assert the `heating_hour` rows. They test behaviour, not the internals of the estimators. No separate unit tests for the learners.

Scenarios, one per behaviour (each also the BDD red-green step):

- an hour of 12 ON polls and no gas is estimated at `24/60 x 12 kW`, priced at the rate in force;
- the same hour with metered gas is measured, less the hour-of-day baseline learned from zero-on hours;
- a boost-only hour (`boost_active=1`, `state` otherwise) counts as heating-on;
- an hour with under 24 polls leaves no row, and a later complete poll set writes one;
- late-arriving gas turns an estimated hour into a measured one on the next run;
- a day whose readings are all zero is not taken as measured zero use;
- the effective kW is learned from measured hours and falls back to 12 below 5 such hours;
- a mid-period rate change prices each hour at its own rate, and no rate gives a NULL cost;
- a clock-change day (23 and 25 hours) gives one unique row per UTC hour, with the baseline keyed by London hour of day;
- the first run on an empty table backfills chronologically;
- the current, unfinished hour is never written;
- a missing `heating_status` table writes nothing and does not raise.

Also: a new test beside `tests/test_grafana_dashboards.py` (extending it if it already checks panel and variable shape) asserts the dashboard JSON has the new panel and no `boiler_kw` variable. A test for `register_heating_hour_job` follows the existing registration tests. The panel query itself is checked once, in the final dashboard review, on a throwaway database copy (never on the live MariaDB, which has a 512 MB cap). Prior art: `tests/test_heating_job.py`, `tests/heating_history_fixtures.py`, `tests/test_heating_cross_app_reads.py`.

## Out of Scope

- Any forward (future-day) heating cost; that needs the hourly forecast in workstream E.
- The comfort model (D), the thermal model and schedule advice (E), the cost-based gas change panel (F) and the flow-temperature model (G).
- Changes to the cost forecast, the heating model fit, or panels 1, 2, 4, 5 and 6.
- Standing charge in the hourly cost.
- Any attempt to measure burner time directly: `state='ON'` is thermostat demand (see ADR-0033).
- Pruning `heating_hour`.

## Further Notes

- The 12 kW fallback comes from one test evening (2026-10-06); the learned kW replaces it as soon as 5 clean on-hours exist.
- `state='ON'` can overstate true burner time, so the learned kW is an average, not a per-hour truth.
- The hot-water split assumes hot-water use in heating hours is like that in no-heating hours of the same hour of day; it is weakest on winter mornings. Both choices are labelled estimated.
- The hourly job reads about 4 days of polls per run (about 2,900 rows). The first-run backfill reads one London day at a time to keep memory small on the Pi.

---
status: accepted
---

# Hourly heating cost learns a hot-water baseline and the boiler's effective kW

The gas dashboard prices heating hour by hour. Octopus's meter measures all gas, and the combi boiler's one burner serves both radiators and hot water, with nothing to tell them apart; Hive reports only thermostat demand (`state='ON' OR boost_active=1`, see ADR-0030's "heating on" definition), not burner time. We decided octopus-app computes one `heating_hour` row per UTC hour (hourly job, recomputing the last four days) and learns two numbers from the household's own hours, over a trailing 28 days: a **hot-water baseline** per hour of day (the median measured gas in hours with no heating-on minutes) which is subtracted from measured gas in heating hours, and an **effective boiler kW** (the sum of heating kWh over the sum of on-hours, from hours with at least 20 minutes on) used to estimate hours that have no measured gas yet. Each row is labelled measured or estimated, and an hour with fewer than about 80% of its polls is left out rather than shown as zero. Cost is the gas unit rate only, no standing charge.

## Considered Options

- **All gas in heating hours, no split.** Exact to the meter, but overstates heating by whatever hot water ran in the hour.
- **A typed `boiler_kw` variable (default 12).** 12 kW came from one test evening (2026-10-06), and the real draw drifts with modulation and, later, flow temperature (workstream G). Rejected, and the variable is deleted.
- **Pure Grafana SQL, no table.** Heavy on a Pi capped at 512 MB, and measured hours would vanish when raw half-hourly gas is pruned at 45 days (ADR-0003). A stored row keeps its measured figure.
- **hive-app writes the table.** It has no gas or rates; the concept would be split across two apps.

## Consequences

- The split is an estimate: the baseline assumes hot water and cooking use is similar in heating and non-heating hours of the same hour of day, which is weakest in winter mornings. Every figure is labelled estimated or measured.
- `state='ON'` is demand, so on-minutes can exceed true burner time; the learned kW absorbs the average of that, not the hour-to-hour error.
- octopus-app reads `heating_status` through a hand-mirrored Core table, the pattern already used for hive-app's weather tables.
- With too little evidence the kW falls back to 12 and the rows stay estimated.

# Gas, heating and weather dashboard, wave 1

## Problem Statement

The existing Grafana dashboards cover electricity ("pi-desktop: electricity-monitoring", whose repo copy is `data/grafana/dashboard.json`, titled "octopus-energy") and the Pi itself ("pi-desktop: monitoring"). Gas, heating and weather have no dashboard: gas panels from earlier drafts were dropped from the export, and the heating and weather data hive-app now collects (thermostat readings every 2 minutes since 2026-10-03, hourly outdoor weather since 2026-10-06) cannot be seen. The original issues for this (#512, #513, #514) were written before the data and the questions were understood: they name a `heat_on_demand` signal that is not a heating-active signal, reference a queries file path that has since moved, and assume a threshold we now learn from data.

## Solution

A new, separate Grafana dashboard of eight chart panels that work from data we already hold or are about to collect (wave 1), documented and imported like the existing one. Wave 2 (the model-driven panels) follows in its own spec. The existing dashboards are not changed in any way. Slice 4 of 5; some panels depend on slices 1 and 2.

## User Stories

1. As the account holder, I want indoor, target and outdoor temperature on one chart, so that I can see whether the schedule did what I expect, with the gap between indoor and target showing when heat was wanted.
2. As the account holder, I want daily gas beside the outdoor temperature, switchable between kWh and pounds (with the standing charge shown apart from the unit cost), so that I can see how gas follows the weather and what it costs.
3. As the account holder, I want a gas heatmap by hour and weekday like the electricity one, so that I can read gas use against my known heating schedule.
4. As the account holder, I want monthly gas totals for this year against last year, so that I can see the broad trend.
5. As the account holder, I want the gas billing-period projection (cost so far against the projected total), so that I know what this period's bill is heading for.
6. As the account holder, I want boost minutes per day and the estimated extra gas cost of those minutes, so that I can decide whether the boost habit is worth it.
7. As the account holder, I want the minutes per day the room was meaningfully below its target, so that I can tell when a saving has cost comfort or the warm-up starts too late.
8. As the account holder, I want daily outdoor temperature (minimum, mean, maximum) with sunshine, so that I have the weather context behind everything else.
9. As the account holder, I want the heating-on definition checked once against real gas, so that I know the thermostat signal matches what the meter saw.

## Implementation Decisions

- **Separate dashboard and house style.** A new dashboard file beside the existing one, with its own documented queries (the existing queries document is not edited) and its own manual import. Working title "pi-desktop: gas-heating-monitoring" (renameable). **All panels are charts, no tables**; the one non-chart panel is the billing-period predictor (a bar gauge, like "Billing Period Progress"). It has its own variables and defaults (a multi-week window), the same data source (its Database field must be `home_monitoring`), and London local-day bucketing for daily figures ([ADR-0010](../adr/0010-local-day-bucketing-python-vs-sql.md)); all stored times are UTC.
- **Complete days.** Daily outdoor values use only complete London days (every hour present, live preferred over archive, [ADR-0029](../adr/0029-weather-history-backfilled-hourly-deduplicated-and-labelled-by-source.md)); days without a settled gas figure (zero or unsettled rows, the 1 to 2 day lag, known upstream gaps) are gaps, never zeros.
- **Heating-on.** Wherever a panel needs it: `state = 'ON'` or boost active (the thermostat's demand signal verified on 2026-10-06); minutes are counted as readings times the 2-minute poll interval.
- **Panel 1, temperatures.** Time series of indoor, target (a step line, since a target holds until changed) and outdoor temperature; three lines only (the gap between indoor and target is the demand).
- **Panel 2, daily gas with outdoor temperature.** Bars of daily gas with the daily mean outdoor temperature as a line on a second axis; a dashboard switch for kWh or pounds (pounds split into unit cost and standing charge from the gas agreement and rates); extended 7 days ahead showing the forecast temperature (the model's expected-gas bars are added in wave 2).
- **Panel 3, gas heatmap.** Average gas per time slot by weekday, London time, the last 45 days (the raw retention), mirroring the electricity "Consumption Heatmap".
- **Panel 4, monthly gas.** Monthly gas totals, this year against last, mirroring "Monthly Total Consumption".
- **Panel 5, predictor.** The latest gas row of the cost forecast (cost so far and projected total), mirroring "Billing Period Progress"; it shows whatever the cost forecast currently computes (the model changes it in slice 3).
- **Panel 6, boost.** Bars of boost minutes per day, plus the estimated extra gas cost as a second series on its own axis: minutes / 60 x the boiler's gas input (kW) x the gas unit rate. The boiler's gas input is a dashboard variable (the user supplies the make and model or kW); until set, the panel shows minutes only. Labelled "estimated; assumes the boiler fires at full output".
- **Panel 7, minutes below target.** Minutes per day with a comfort target active (target at or above a dashboard setting, default 15 C) and the room more than a dashboard margin (default 1.0 C) below it.
- **Panel 8, outdoor band with solar.** Daily minimum, mean and maximum outdoor temperature as a band, with sunshine (and radiation) on a second axis; needs the solar data from slices 1 and 2.
- **Validation (not a panel).** Compare the heating-on windows recorded on 2026-10-06 (about 19:14 to 19:21 and 19:24 to 19:27 UTC) with the half-hourly gas for the 19:00 to 19:30 UTC slot once it arrives, and post the result on the issue. This replaces the permanent half-hourly-with-shading panel originally proposed in #513.
- **Docs.** The new queries document records, per panel, the title, type, data used and the query, plus the schema assumed and the import steps; the glossary and README gain a short note.

## Testing Decisions

- Dashboard SQL is not unit-tested (a standing decision for Grafana work in this repo). Instead: the dashboard JSON is valid and has a unique title and uid; each panel's query is run read-only against the live database (or a seeded real MariaDB) and its output checked by eye for a known day; the heatmap and monthly queries are checked against the existing electricity equivalents' shape.
- Verify DST days, a gas gap day, and a day with no settled gas show as designed.
- Prior art: the existing queries document and the way earlier dashboard changes were verified (for example the estimated-rates panel work).

## Out of Scope

- The model-driven panels (wave 2), data-health tiles, a heating-hours panel, a half-hourly gas-with-shading panel, heat retention, and any change to the existing dashboards or their documentation.
- Deploying or importing to the Grafana on the Pi (a separate, asked-first step; app changes deploy first, then the manual import).

## Further Notes

- **Supersedes** issues #512 (panel 1), #513 (replaced by the validation task) and #514 (replaced by the model panels and year-on-year in wave 2); they are not closed without the user's say-so.
- Outstanding input from the user: the boiler's make and model, or its gas input in kW (panel 6).
- Panels needing weather history (2 and 8, and the weather columns elsewhere) wait on slices 1 and 2; panels 1, 3, 4, 5, 6 and 7 can start earlier.

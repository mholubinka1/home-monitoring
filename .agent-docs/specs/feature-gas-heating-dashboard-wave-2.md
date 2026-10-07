# Gas, heating and weather dashboard, wave 2: the model-driven panels

## Problem Statement

Wave 1 shows the data. What the household actually asked for is an absolute answer: when was heating not required or overused, is the house getting more or less efficient, and what would a lower setpoint save? Those answers come from the learned [Heating Model](../adr/0030-learned-heating-model-and-its-adoption-by-the-cost-forecast.md), which is built in slice 3; until it exists there is nothing for these panels to read.

## Solution

Six panels on the same separate dashboard, reading the model's three tables only (no model maths in Grafana): the model chart, "where the gas went", baseload-adjusted efficiency, weather-normalised year-on-year, the setpoint saving, and the 7-day expected-gas extension of the wave-1 daily gas panel. Slice 5 of 5; depends on slice 3 and on wave 1.

## User Stories

1. As the account holder, I want to see my learned threshold, baseload and slope drawn over my real days, with a label saying whether the thermostat confirms it, so that I can judge the fit before trusting anything built on it.
2. As the account holder, I want each day's gas stacked into baseload, expected heating, normal variation and possible or clear waste, with totals for the time range I choose, so that I can see when and how much heating was wasted.
3. As the account holder, I want the same chart in pounds as well as kWh, so that waste is a money figure.
4. As the account holder, I want an efficiency number with the weather and the baseload removed, as a rolling trend, so that I can tell whether a change helped or the house is getting worse.
5. As the account holder, I want this year's efficiency against last year's, week by week, so that I know whether I really used less or it was just warmer.
6. As the account holder, I want the estimated saving from a thermostat one degree lower, month by month, beside my average target, so that I can decide whether to turn it down.
7. As the account holder, I want expected gas for the next 7 days on the daily gas chart, so that I can plan the week.

## Implementation Decisions

- **Reads only.** Every panel reads `heating_model`, `heating_day_verdict`, `heating_forecast_day` (and the raw tables wave 1 already uses); no fitting or verdict logic in SQL. The same house style as wave 1 (charts only, separate dashboard, London days, gaps not zeros, the 'estimated' label wherever the model is shown).
- **Model chart.** Daily gas against effective temperature as points with the fitted line drawn from the latest model; the legend carries baseload, threshold, slope and the label; the date the model was last fitted is shown. Grafana's XY chart is the intended type; verify it suits at build, and if it does not, document the fallback chosen.
- **Where the gas went.** Stacked daily bars of five slices that sum to actual gas, with excess and avoidable each in two shades (possible, clear), legend totals for the selected range, switchable between kWh and pounds. Away days and unsettled days show as neutral gaps.
- **Baseload-adjusted efficiency.** Rolling 7-day (gas above baseload) per degree-day, hidden for windows with too few degree-days to measure.
- **Weather-normalised year-on-year.** Gas above baseload per degree-day, this year against the same weeks last year, complete weeks only (like the existing year-on-year panel). **Open design question for this branch:** the baseload itself changed between the last two years (prototype: about 5.6 versus 8.2 kWh/day), so subtracting one current baseload from last year's days would mislead; the recommended approach is to subtract each period's own baseload, estimated from that period's warm days, and to confirm this in the branch's design session.
- **Setpoint saving.** Monthly bars of the estimated saving (pounds and kWh) from a thermostat 1 C lower, being the model slope times the month's number of needed days, with the average target on heating days as a line.
- **Daily gas extension.** The wave-1 daily gas panel gains lighter "expected gas" bars for today and the next 7 days from the forecast-day table, beside the forecast temperature.
- **Coverage of history.** The verdict table must cover the span the panels show (including last year for year-on-year); if slice 3 only judges the rolling window, extend it here or compute the older periods separately, and record the choice.
- **Docs.** Extend the new queries document per panel, and add a short "how to read this" note linking to the model explainer.

## Testing Decisions

- As for wave 1: valid dashboard JSON; each query run read-only against the live tables and checked for a known day (the stacked slices sum to the day's gas; a not-needed day shows avoidable only when clearly warm; the label matches the model table; the pounds switch matches the daily cost); checks for DST days, an away day, a day without settled gas, and a week with too few degree-days.
- Prior art: wave 1's checks and the existing year-on-year panel's complete-weeks logic.

## Out of Scope

- Changing the model, its parameters or its tables (slice 3); the data-health tiles, heating-hours, threshold-over-time and heat-retention panels (cut); deploying or importing to Grafana (a separate, asked-first step).

## Further Notes

- Supersedes the model-related part of #514 and finishes the work started by #512 and #513; none are closed without the user's say-so.
- The panels carry the model's own caveats: totals are a floor, verdicts are coarse (R-squared about 0.7), and the label stays "estimated" until the thermostat agrees.

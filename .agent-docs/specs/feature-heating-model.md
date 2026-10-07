# The heating model: learning when heating is needed, and judging every day against it

## Problem Statement

The household wants an absolute answer to "when was heating not required, or overused?", and a gas cost forecast it can trust. Today the cost forecast's gas regression assumes a fixed 15.5 C base on each day's maximum temperature over a 7-day window, and nothing tells the household whether a given day's gas was reasonable for the weather. On this household's own data the real threshold is nearer 12.6 C, the day-to-day noise grows sharply on cold days, and a plain fit also shows that the house changed between the last two years ([prototype findings](../research/heating-model-prototype.md)). No existing view separates gas the weather required from gas that was wasted.

## Solution

A learned **Heating Model** computed by a daily octopus-app job: it fits the household's baseload, heating threshold and slope from a rolling 12 months of gas and weather, judges every day (needed or not, and how far over expected), projects the next 7 days, and records whether the thermostat's own behaviour confirms the threshold. The cost forecast and the gas dashboard read its three small tables. The model also ships with a plain-language explainer for non-specialists. Slice 3 of 5; depends on slices 1 and 2 ([ADR-0030](../adr/0030-learned-heating-model-and-its-adoption-by-the-cost-forecast.md), [ADR-0029](../adr/0029-weather-history-backfilled-hourly-deduplicated-and-labelled-by-source.md)).

## User Stories

1. As the account holder, I want to know the outdoor temperature below which my heating is really needed, learned from my own history, so that I can set a sensible rule such as "no heating above X".
2. As the account holder, I want every past day judged as needed or not needed, so that I can see which days heating was avoidable.
3. As the account holder, I want gas above what the weather justified split into "possible" and "clear" waste, so that I can tell noise from real waste without being alarmed by normal variation.
4. As the account holder, I want waste totals to be a defensible floor, so that I can trust a figure before acting on it.
5. As the account holder, I want each day's gas to split exactly into baseload, expected heating, normal variation and waste, so that a chart of it adds up.
6. As the account holder, I want holiday or low-use days left out of the fit and shown neutral, so that they do not distort the baseload or look like savings.
7. As the account holder, I want to know whether the learned threshold is "estimated" or "confirmed" by my thermostat's own behaviour, so that I know how far to trust it.
8. As the account holder, I want expected gas for the next 7 days, so that I can plan the week and the budget.
9. As the account holder, I want the gas cost forecast to use this model, so that the monthly gas predictor reflects the weather properly, with a safe fallback if the model cannot be fitted.
10. As an operator, I want the model refitted weekly and every day re-judged consistently, so that the chart always uses one yardstick and shows when the model was last fitted.
11. As a reader who is not a data scientist, I want a plain-language explanation of how the model works, with links to the concepts, so that I can understand and challenge the numbers.
12. As a maintainer, I want the fitting, the verdict rules and the guards to be pure, well-tested functions, so that the maths can be changed safely.

## Implementation Decisions

(All parameters below are named constants in one place, documented, and changed in code.)

- **Inputs.** Daily gas totals (zero or unsettled rows are missing data, never zero use); London-day mean temperatures from hourly weather, taken only for **complete days** (every hour present, live preferred over archive per hour, 23 or 25 hours on daylight-saving days); the forecast daily mean for projection; heating readings from hive-app's `heating_status` for the thermostat check.
- **Effective temperature.** `(1 - w) * today's mean + w * yesterday's mean`, with `w` fitted within a bounded range (0 to 0.8).
- **Fit.** Over the rolling 12 months (365 days), `gas = baseload + slope * max(0, threshold - effective temperature)`. Search a grid of thresholds (about 8 to 18 C, step 0.1) and weights (step 0.05); for each, ordinary least squares gives baseload and slope; keep the smallest squared error. A plain fit to all included days (typical behaviour, no trimming). Baseload is constant over the year in this version; the leftover errors are checked for a winter/summer pattern and reported, and a seasonal term is added only if the evidence appears.
- **Away days.** After a first fit, days with gas below about a third of the baseload (about 2.5 kWh) are excluded and the model is refitted once; they are stored as "away" and shown neutral.
- **Uncertainty.** About 300 bootstrap resamples of the days give a range for the threshold; the upper end defines the borderline rule below.
- **Noise.** Each day's allowed wobble scales with its heating need: residual size regressed on the day's degree-day amount, with a floor; margins are multiples of that scaled standard deviation.
- **Verdict.** For day d with expected gas E and residual r = actual - E and scaled standard deviation s: the day is **needed** if its effective temperature is below the threshold's upper uncertainty end, otherwise not needed. "Possible" = the part of r between 1s and 2s; "clear" = the part of r beyond 2s; the first 1s of overshoot stays inside "needed" as normal variation. On needed days these are **excess**; on not-needed days **avoidable**. Slices (baseload, expected heating, normal variation, possible, clear) sum exactly to actual gas. Days with gas below the away rule are neutral.
- **Setpoint saving.** The estimated gas saved by a thermostat 1 C lower over a period is the slope times the number of needed days in it.
- **Thermostat check and label.** A heating day is a London day with at least 30 minutes of scheduled (non-boost) demand and at least about 90% of its 720 two-minute readings present. The thermostat-side threshold is the effective temperature that best separates heating days from non-heating days. The model is **confirmed** when it lies within about 1.5 C of the gas-based threshold with about 15 days of evidence on each side; otherwise **estimated**.
- **Fit guards (usable or not).** Unusable, with the reason stored, when there are fewer than about 150 usable days, fewer than 30 days on either side of the threshold, R-squared below 0.5, the threshold at the edge of the search range, or a non-positive slope.
- **Job.** A daily octopus-app job, scheduled after the gas summary refresh and recorded in `job_run` like the others: refit if the latest model is older than 7 days, recompute every day's verdict in the window with the latest model, project 7 days ahead from the forecast daily mean, write the tables. Also runs once at startup when no model exists and enough data does.
- **Tables (three, small, additive, shared database).** `heating_model` (one row per refit: when fitted, window, days used, baseload, slope, threshold, upper threshold, blend weight, noise terms, R-squared, thermostat-side threshold and its evidence counts, label, usable flag and reason). `heating_day_verdict` (one row per day: date, mean and effective temperature, actual and expected gas, needed, away, and the five slices, tied to the model that produced it). `heating_forecast_day` (one row per future day: forecast mean, effective temperature, expected gas, computed at).
- **Cost forecast adoption.** The cost forecast reads the latest usable model (and the forecast-day table) for each remaining day's expected gas, falling back to today's flat-average behaviour for the whole period when there is no usable model. The old regression, its constants and its now-unused weather reader are removed. The pull request shows the old and new projections side by side.
- **Explainer.** `docs/heating-model.md`, written for people who are not data scientists: an ELI5 of what the model does and why, built around the real chart (flat when warm, rising when cold) and one worked example; each idea (degree-days, baseload, threshold, least squares, standard deviation, confidence range, residuals, thermal memory, away days, estimated vs confirmed) explained in plain words with a "learn more" link; what the model cannot tell you; how to read each dashboard panel built on it. External links are real, checked by the repository's link checker. See Further Notes for the verified link list and the required outline.
- **Domain docs.** ADR-0030 and the glossary terms (Degree-day, Effective Temperature, Heating Model, Baseload, Heating Threshold, Heating Verdict) are written in the planning change; update them if the build changes a decision.

## Testing Decisions

- Pure functions (effective temperature, fit, guards, noise scaling, verdict and decomposition, thermostat check) tested with synthetic data where the truth is known: the fit recovers a planted threshold, baseload and slope; zero rows are treated as missing; away days are excluded and neutral; each day's slices sum to its gas; borderline days are needed; flag counts behave as specified; a degenerate or too-small input is unusable with the right reason.
- Job and tables tested through the existing octopus-app seams with the SQLite fixture and real-MariaDB tests in `libs/common` where needed (skipped locally without Docker, run by CI): weekly refit cadence, daily recompute, projection, `job_run` recording, Schema Sync adding the tables.
- Cost forecast: existing gas tests migrated to the model; fallback to flat average when no usable model; the side-by-side comparison is reproducible.
- Explainer: the repository's Markdown link check passes; every concept in the required outline is present and linked.
- Prior art: the cost forecast gas regression tests, the consumption summary job tests, and the job-run recording tests.

## Out of Scope

- Dashboard panels (slices 4 and 5), seasonal baseload, a weekend term, solar or wind in the model (the collected solar is only examined, not fitted), occupancy, hardware, and any change to the electricity forecast.
- Deploying to the Pi (a separate, asked-first step).

## Further Notes

- Real-data reference values for sanity checks are in the [prototype findings](../research/heating-model-prototype.md): threshold about 12.3 to 12.6 C, baseload about 7 kWh/day, slope about 3.6 to 3.9 kWh per degree-day, R-squared about 0.68 to 0.72. The two most recent years differ materially, by design.
- Choices made against the recommendation, recorded for transparency: adopting the model in the cost forecast immediately (the fallback and the side-by-side check are the safeguards), and standard-deviation multiples for the flag levels (they flag about 7% of cold days as "clear" because gas is right-skewed; raise the "clear" level if that feels too common).
- Explainer outline (for plain readers): 1) what this is for and the picture; 2) the one-line idea; 3) baseload; 4) the threshold; 5) the slope; 6) why yesterday matters; 7) judging a day (expected, normal wobble, possible, clear); 8) why the totals are a floor; 9) away days; 10) estimated vs confirmed; 11) what it cannot tell you; 12) reading the dashboard panels; 13) glossary and further reading. Concept links, all verified to resolve on 2026-10-07: [heating degree day](https://en.wikipedia.org/wiki/Heating_degree_day), [linear regression](https://en.wikipedia.org/wiki/Linear_regression), [ordinary least squares](https://en.wikipedia.org/wiki/Ordinary_least_squares), [segmented regression](https://en.wikipedia.org/wiki/Segmented_regression), [standard deviation](https://en.wikipedia.org/wiki/Standard_deviation), [errors and residuals](https://en.wikipedia.org/wiki/Errors_and_residuals), [heteroscedasticity](https://en.wikipedia.org/wiki/Heteroscedasticity), [skewness](https://en.wikipedia.org/wiki/Skewness), [confidence interval](https://en.wikipedia.org/wiki/Confidence_interval), [bootstrapping](https://en.wikipedia.org/wiki/Bootstrapping_%28statistics%29), [coefficient of determination (R-squared)](https://en.wikipedia.org/wiki/Coefficient_of_determination), [thermal mass](https://en.wikipedia.org/wiki/Thermal_mass), [reanalysis](https://en.wikipedia.org/wiki/Reanalysis), and the original building-energy method this model follows (the three-parameter heating change-point model of [PRISM](https://www.aceee.org/wp-content/uploads/proceedings-1980-2020/1986/SS86_Panel9_Paper_04.pdf)).

# Heating model prototype: what the real data showed

A throwaway analysis run on 2026-10-07, before the model was designed in detail. It informs [ADR-0030](../adr/0030-learned-heating-model-and-its-adoption-by-the-cost-forecast.md) and the model spec. The scripts were not kept in the repo; the method is short enough to reproduce from this note.

## Method

- **Gas:** `daily_consumption_summary` rows for gas (778 days from 2024-07-24 to 2026-10-05), read-only from the live database.
- **Weather:** Open-Meteo's historical archive, daily mean, maximum and minimum temperature for the London local day, at the location rounded to one decimal place of latitude and longitude (about 11 km, finer than the weather grid), 2024-07-24 to 2026-09-30.
- **Model:** `gas = b0 + b1 * max(0, tau - T)`. For each candidate `tau` (6 to 20 C, step 0.1) compute the one-variable least-squares fit of gas on `x = max(0, tau - T)` and keep the `tau` with the smallest squared error. Noise: residual size regressed on `x`. Threshold uncertainty: 300 bootstrap resamples of the days.

## Data cleaning findings

- **63 of 778 daily gas rows are exactly 0.0 kWh.** They are missing readings (for example 13 to 17 March 2026, and the latest unsettled day), not real zero use. A zero in this table must be treated as missing. 711 days remained after also dropping days beyond the weather range.
- **39 days are under 2.5 kWh**, including about nine consecutive days in July 2025 (0.1 to 1.4 kWh): real "away" days (a holiday), where hot water and cooking stop. They lower the fitted baseload by about 10% (6.97 versus 7.69 kWh/day when excluded) and should be excluded and shown neutral.

## Results (711 days, two winters)

| Basis | Threshold | Baseload (kWh/day) | Slope (kWh per degree-day) | Residual std (kWh) | R-squared |
| --- | --- | --- | --- | --- | --- |
| Daily mean | 12.6 C | 6.97 | 3.55 | 8.18 | 0.680 |
| Daily maximum | 15.9 C | 7.06 | 3.28 | 8.07 | 0.689 |
| 70% today + 30% yesterday | 12.4 C | 6.87 | 3.79 | 7.73 | 0.714 |
| Blend, best weight 0.55 on yesterday | 12.3 C | 6.86 | 3.88 | 7.60 | 0.724 |

- **Mean versus maximum is a wash** on fit; the larger gain is adding yesterday's temperature (thermal memory). The best weight on yesterday differs by year: 0.75 (last 12 months), 0.40 (the 12 months before), 0.55 over both.
- **The threshold is well pinned:** 90% of bootstrap resamples gave 12.0 to 13.4 C (mean basis); fits within 1% of the best error span 11.9 to 13.5 C.
- **Noise grows with the cold.** Residual standard deviation: 3.7 kWh on warm days; 8.4 (0 to 3 degree-days), 10.7 (3 to 6), 13.5 (6 to 9), 13.9 (9 or more). A fixed margin therefore under-flags warm days (flagging 8 of 366 warm days versus 37 when the margin scales with the cold).
- **The noise is right-skewed.** A "2 standard deviations" cut-off flags about 7% of cold days (24 of 345) rather than the 2% a normal curve predicts.
- **Trimming the highest days** (drop days above 2 standard deviations and refit, four passes) moves the threshold from 12.6 to 11.5 C and the residual standard deviation from 8.2 to 5.3: that describes the household's most efficient days, not its typical ones, and is one-sided.
- **The two years differ.** Last 12 months: threshold 13.5 C, baseload 8.2, slope 3.2, R-squared 0.62. The 12 months before: 12.0 C, 5.6, 3.9, 0.76. Persists after excluding the low-use days.
- **Smaller effects:** warm-day baseload April to September 6.6 versus October to March 7.6 kWh/day (small sample); weekends run about 0.8 kWh above the model and weekdays about 0.3 below.

## Caveats

Archive temperatures are reanalysis values for the area, not the house, and the live hourly pipeline will differ slightly. R-squared of about 0.7 means about 30% of day-to-day variation is not explained by temperature alone (wind, sunshine, behaviour, hot water), so verdicts are deliberately coarse.

## Gas gaps: where the missing data comes from (investigated 2026-10-07)

The Octopus API was asked, read-only, what it reports today for every gas day it serves, and compared with `daily_consumption_summary`.

- **The API serves gas only from 2024-10-09** (706 days to 2026-10-06). We hold gas back to 2024-07-24, so the first 11 weeks cannot be re-checked.
- **Our table agrees with the API.** Of the days from 2024-10-09 to 2026-10-04 (726), 643 stored positive days match the API within 5% (none differ by more than 1%), and no stored zero is stale against the API.
- **The gaps are upstream:** 61 days are all-zero in the API itself (runs of 11 days from 2025-04-24, 7 from 2025-07-01, 6 from 2025-08-07, about 16 in March 2026), 22 days have no data in the API, and 13 days are partial (fewer half-hours than expected, for example 29 of 48 on 2025-03-12) but stored as a smaller positive total, so they silently understate. 630 days are complete. Electricity has no gaps at all (805 of 805 days, none zero), so it is the gas meter or its link.
- **Zero half-hours inside a day are normal** (the boiler is idle for many slots), so they do not indicate dropouts; only fewer readings than expected, or an all-zero day, mark a gap.
- **Two effects on our side:** the summary stores only the day's total, so a partial day looks real; and the summary refreshes weekly, so the newest days read zero until the next run (2026-10-05 read 0.0 while its raw rows totalled 6.9 kWh).
- **Cause unconfirmed.** Multi-day runs fit a lost link between the gas meter and the communications hub, but that is a hypothesis; Octopus could confirm it.

## The baseload moves (investigated 2026-10-07)

- **Warm-day gas by month** (days at least 1 C above the threshold, at least 2.5 kWh): about 5.5 in summer 2024, 5.4 to 6.3 in May to June 2025, 6.9 to 7.1 in August to September 2025, then 9.1 in May to June 2026 and 9.4 in September 2026. A seasonal swing (low in June, higher into autumn and winter) sits on top of a year-on-year lift. Winter baseload is barely observable (about 7 to 10 warm days in a stretch of winter months).
- **Sliding six-month fits** gave baseloads from 4.5 to 10.9 kWh/day with a stable slope of about 3.7 to 4.1; fitted once per year the baseload was about 6.55 (Oct 2024 to Sep 2025) and 8.63 (Oct 2025 to Sep 2026).
- **Held-out test** (train on the 12 months before each of 15 test months, predict that month; errors in kWh/day):

| Baseload method | Error, all months | Error, cold months | Cold-month bias |
| --- | --- | --- | --- |
| Constant | 6.03 | 13.11 | -5.9 |
| Trend plus seasonal swing (fitted) | 8.03 | 13.05 | -6.4 |
| Monthly warm-day means joined by lines | 6.00 | 12.58 | -4.4 |
| Moving average, 45 days | 6.10 | 13.40 | -6.0 |
| Moving average, 90 days | 6.07 | 13.04 | -5.5 |
| Moving average, 120 days | 6.01 | 12.74 | -5.3 |

- **Reading it:** the gains from moving the baseload are modest in raw prediction and within noise across window lengths (15 test months, about 13 kWh/day of day-to-day noise on cold days); a longer window did slightly better in cold months. A fitted trend plus seasonal swing is clearly worse on a 12-month window because it cannot separate a trend from the seasons. All methods under-predict cold months when trained on the previous year because this year's household uses more gas (behaviour change, not a modelling error). The case for a moving baseload is that it demonstrably moves, which makes baseload-adjusted comparisons and warm-day verdicts like for like.

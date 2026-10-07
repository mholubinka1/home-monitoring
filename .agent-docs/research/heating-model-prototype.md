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

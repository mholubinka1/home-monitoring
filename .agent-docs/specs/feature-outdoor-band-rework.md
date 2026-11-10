# Outdoor band rework

## Problem Statement

The Daily Outdoor Temperature Band on the gas/heating dashboard shows only what the weather has already done (minimum, mean and maximum) and says nothing about the house or the days ahead. I cannot see how warm the house was on the days when it was cold, and I cannot see how cold the coming days are expected to be. The mean line also duplicates the one on the Daily Gas chart.

## Solution

Rework the band into a picture of "weather versus house", day by day:

- Daily outdoor **minimum** and **maximum** as lines, for complete London days, as today.
- A **Waking-Hours Indoor Average** line, so I can see the house temperature next to the weather.
- The forecast **minimum** and **maximum** for today and the coming days as dotted lines continuing the observed ones.
- The mean line leaves this panel. It stays on the Daily Gas chart, which is unchanged.

Sunshine and solar radiation stay recorded and are never shown or used.

## User Stories

1. As the householder, I want the band to show the daily outdoor minimum and maximum as lines, so that I can see how cold nights and mild days have been.
2. As the householder, I want a daily average indoor temperature on the same chart, so that I can compare what the house did with what the weather did.
3. As the householder, I want that average to cover only the hours I am awake, so that the cold, unoccupied overnight setback does not drag it down.
4. As the householder, I want Wake Up and Bedtime dashboard variables (defaults 7 and 23), so that I can change the window without a code change.
5. As the householder, I want a day plotted only if at least 80% of the polls expected in the waking window exist, so that a Hive outage does not produce a misleading figure.
6. As the householder, I want the forecast minimum and maximum for today onward as dotted lines, so that I can see the cold coming.
7. As the householder, I want forecast lines drawn only from today onward, so that past days always show what was observed.
8. As the householder, I want the forecast to use the latest stored forecast for each day, so that it reflects the newest prediction.
9. As the householder, I want days whose stored forecast has no minimum yet (fetched before this change) to be omitted from the dotted minimum line, so that nothing is invented.
10. As the householder, I want the mean line to leave the band and stay on the Daily Gas chart, so that the same figure is not drawn twice.
11. As the householder, I want sunshine and solar radiation still recorded but never shown or used, so that the data is there if I change my mind.
12. As the operator, I want the new column added by Schema Sync with no manual step, so that deploying the app is enough.

## Implementation Decisions

- **hive-app forecast fetch:** the daily forecast request also asks Open-Meteo for the daily minimum temperature. The response model accepts it as optional, with the same handling of an omitted or mismatched-length array as the mean (omitted means nulls, a length mismatch is rejected).
- **Forecast day model and storage:** the Weather Forecast day gains an optional minimum temperature, stored in a new nullable `min_temp` column on `weather_forecast`. Schema Sync adds the column to the existing table (additive). Earlier rows keep null.
- **octopus-app:** unchanged. Its hand-written mirror of `weather_forecast` only needs the column if octopus-app reads it, and it does not. Grafana reads the database directly.
- **Panel 5 query and series:**
  - Observed daily outdoor minimum and maximum, keeping the existing complete-London-day rule and the live-over-archive preference. The mean series is removed from this panel.
  - Waking-Hours Indoor Average: the mean `current_temp` of Heating Status polls whose London local time is between the Wake Up and Bedtime hours. A day is returned only if the poll count is at least 80% of the expected count (window hours x 30). The window is assumed to sit within one day (Bedtime later than Wake Up).
  - Forecast minimum and maximum for target dates from today (London) onward, from the stored forecast for each date (one row per source and date, overwritten by each fetch, so it is the latest fetch), leaving a date off a line when that forecast has no value for it, drawn dotted. The series are drawn dotted through per-series overrides, with the same colours as the observed lines.
- **Variables:** two new Grafana text-box variables (like Comfort target), Wake Up (default 7) and Bedtime (default 23), whole hours. Their values enter the SQL under the existing escaping convention for text-box values.
- **Panel text:** the panel description is updated to match (the band is no longer min, mean and max). The title, "Daily Outdoor Temperature Band", stays: it never named the mean and the outdoor lines are still the panel's core.
- **Panel 2 and panel 1:** unchanged.
- **Docs:** `data/grafana/mariadb/gas-heating-queries.md` is updated for panel 5. The glossary gains Waking-Hours Indoor Average and notes that Weather Forecast now carries a nullable minimum (already done in the design session).
- **Deploy order:** deploy hive-app first so `min_temp` exists and fills, then import the dashboard.
- **Not an ADR:** additive and reversible.

## Testing Decisions

- Test external behaviour at existing seams, not internals.
- **hive-app Open-Meteo to MariaDB seam** (prior art: `test_weather_solar_and_forecast_mean.py`, `test_weather_forecast_seam.py`):
  - the forecast request asks for the daily minimum;
  - each day's minimum is stored beside the maximum and mean;
  - a response without minimums stores nulls;
  - a mismatched-length minimum array is rejected.
- **Schema Sync** (prior art: `test_weather_solar_schema_sync.py`): an existing `weather_forecast` table without `min_temp` gains it on startup, keeping its rows.
- **Dashboard JSON seam** (`apps/octopus-app/tests/test_grafana_dashboards.py`): the Wake Up and Bedtime variables exist with the agreed defaults and every variable used is defined; panel 5 has the observed minimum and maximum, the indoor average and the two forecast series, with the forecast series drawn dotted; the mean is no longer on panel 5; panel 2 still has its outdoor mean line; the existing generic checks (time column, override aliases, escaping) still pass.
- The panel 5 SQL is not executed in CI. It is checked once by hand against a throwaway copy of the data, never the live database (it is heavy and the Pi has a 512 MB cap).

## Out of Scope

- Hourly forecast and indoor min/max lines (a later workstream).
- Showing or using sunshine and solar radiation.
- Any change to panels 1, 2, 3, 4 or 6.
- The comfort-temperature model, hourly heating cost and the rest of workstreams C to G.
- Backfilling `min_temp` for past forecast rows, and any change to octopus-app.
- Tightening `.agent-docs/context.md` to the glossary format rules.

## Further Notes

- Design decisions came from the 2026-11-10 session: waking hours chosen over the all-day mean, with variables for the window; the mean stays on panel 2; 80% coverage for the indoor average.
- Past days' forecast minimum is never read, so no gap rule is needed for old rows.
- After all workstreams are done, the whole dashboard gets one review.

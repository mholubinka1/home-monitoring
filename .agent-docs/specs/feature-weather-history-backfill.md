# Weather history backfill: hourly, from the start of the gas history

## Problem Statement

Live weather collection only began on 2026-10-06, but the heating model needs weather for the whole gas history (daily gas goes back to 2024-07-24, about 26 months, two winters), and the year-on-year and efficiency panels need the same. Without history the model cannot be fitted and the panels are empty for months.

## Solution

A one-off, safely re-runnable command that fills `weather_observation` with hourly weather (every variable we store, plus solar) from 2024-07-24 up to the present, so history joins seamlessly onto the live series. Slice 2 of 5 ([ADR-0029](../adr/0029-weather-history-backfilled-hourly-deduplicated-and-labelled-by-source.md)); it depends on slice 1 (the unique key and the new columns).

## User Stories

1. As the account holder, I want weather history from the first day of my gas history, so that the model learns from two winters.
2. As an operator, I want to run the backfill more than once without creating duplicates or touching live rows, so that a failed or partial run can simply be repeated.
3. As the account holder, I want backfilled rows clearly marked as history, so that I can always tell reanalysis from live readings.
4. As an operator, I want the newest days (which the archive has not reached yet) filled in too, so that history has no hole before the live series.
5. As an operator, I want a short report of which London days are complete and which hours are missing, so that I know the data is ready for the model.
6. As the account holder, I want the backfill to use the same location the live job uses, so that history and live data describe the same place.

## Implementation Decisions

- **Where.** A command in hive-app (which owns the weather tables), run by hand on the Pi, not a scheduled job. It uses the cached Weather Location ([ADR-0028](../adr/0028-weather-location-derived-lazily-from-account-postcode.md)) and fails with a clear message if none exists.
- **Range.** From 2024-07-24 (the first daily gas day; a default that can be overridden) to the latest available hour. Times are requested and stored in UTC, like the live job.
- **Sources.** The historical archive for old days, in sensible chunks (for example a year per request); the forecast service's `past_days` (up to 92) for the recent days the archive has not reached, fetched first so the archive's reanalysis then replaces overlapping hours. Never store forecast (future) hours as observations: truncate to the latest completed hour.
- **Variables.** Hourly temperature, humidity, pressure, wind speed, precipitation, shortwave radiation, cloud cover and sunshine duration. Where the archive only offers a variable daily (sunshine duration is documented that way; confirm at build), store it on the hourly rows only if it can be derived honestly, otherwise leave null and record the limitation in the report.
- **Label and idempotence.** Every backfilled row is labelled `open-meteo-archive` (regardless of which endpoint supplied it) and written with the keyed upsert from slice 1, so a re-run replaces earlier backfilled values and never touches live (`open-meteo`) rows.
- **Failures.** Transient request failures retry with backoff; a chunk that still fails stops the run with a message saying which range to repeat. No coordinates or secrets in logs beyond what the live job already logs.
- **Report.** After the run, print rows written per month, the count of complete London days (23, 24 or 25 hourly readings, live preferred over archive per hour) and the list of days with missing hours.

## Testing Decisions

- External behaviour with HTTP mocked at the boundary and the SQLite fixture; idempotence also against the real MariaDB fixture.
- Good tests: a run stores hourly rows with the archive label; a second run leaves the row count unchanged; live rows for the same hours are untouched; past-days values are replaced by archive values on a later run; future hours are never stored; a mid-run failure leaves earlier chunks intact and the repeat completes; DST-boundary days report 23 and 25 hours correctly; the report lists missing hours.
- Prior art: the existing Open-Meteo client tests and the consumption backfill tests.

## Out of Scope

- Running it against the Pi (a separate, asked-first step) and any scheduling.
- Backfilling forecasts, or any variable we do not already store plus solar.
- The model and dashboards.

## Further Notes

- About 19,500 hourly rows, so size is not a concern. Archive coverage and the exact recent lag vary; verify at build against Open-Meteo's documentation ([historical API](https://open-meteo.com/en/docs/historical-weather-api)).
- The archive is a reanalysis and the live rows are the forecast service's current conditions, so they can differ by about a degree on an hour (unmeasured here).

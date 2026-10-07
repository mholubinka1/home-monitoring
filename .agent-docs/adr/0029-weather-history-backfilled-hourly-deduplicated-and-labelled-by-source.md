# Weather history is backfilled hourly, de-duplicated by (source, time), labelled by source, and a day is complete only if every hour is present

The gas/heating model and dashboard need weather history that matches the whole gas history (daily gas back to 2024-07-24, about 26 months), but `weather_observation` only started filling on 2026-10-06, and it had no uniqueness at all: its only key is an auto-increment `id`, so every write is a plain insert and a re-run (or a backfill overlapping the live job) would silently duplicate rows. We decided: (1) a **unique key on `(source, observed_at)`** plus a keyed upsert (the shared `upsert` only resolves primary-key conflicts), added additively through Schema Sync while the table is nearly empty; (2) a one-off, re-runnable **hourly backfill of every variable we store plus solar** (shortwave radiation, cloud cover, sunshine duration) from 2024-07-24, using Open-Meteo's historical archive for old days and the forecast service's `past_days` (0 to 92) for the recent days the archive has not reached yet; (3) backfilled rows labelled `open-meteo-archive` and live rows `open-meteo`, with readers preferring live where both exist and nothing ever overwriting the other; (4) a London day is **complete** only if every hour of that local day (23, 24 or 25 on daylight-saving days) has a reading from either source, so hours the live job missed are filled by the archive once it catches up; incomplete days are gaps, never partial values; (5) the **daily mean** (not the daily maximum) is the day's temperature, so `weather_forecast` gains a daily mean column.

Surprising without context: the archive is a reanalysis (observations blended with weather models and quality-checked later) while live rows are the forecast service's "current conditions", so the two can differ by about a degree on a given hour (not measured for this location); keeping separate labels records provenance and keeps the mixing confined to the boundary between the two series.

## Considered Options

- **Keep 400 days** (matching the existing panels' `timeFrom: 400d`): rejected; the model fits on a rolling 12 months and should see two winters, and the 400-day figure is a display window, not a data limit.
- **A text `id` built from the time (as `weather_forecast` does)**: rejected; it changes a column type, which Schema Sync never does, so it would need a manual drop-and-recreate on the Pi.
- **Backfill pre-checks only**: rejected; it leaves the live job unprotected against duplicates.
- **One label for everything / replace live with archive once it settles**: rejected; the first loses provenance and risks overwriting live rows, the second changes stored numbers after the fact.
- **Daily summaries only**: rejected; a different method from the live hourly data, and no hourly detail.
- **Tolerant completeness (at least 20 of 24 hours)**: rejected; a missing night biases the mean warm.

## Consequences

- A re-run of the backfill is safe, and so is a live write for an hour that already exists.
- Archive lag varies (a few days), so the newest days rely on the forecast service's `past_days`; days with a missing hour are gaps until it is filled.
- Daily values for a day that is part live and part archive mix two sources for that day.
- The existing cost-forecast regression (daily maximum, [ADR-0030](0030-learned-heating-model-and-its-adoption-by-the-cost-forecast.md) retires it) is unaffected until the model ships.
- Related: [ADR-0005](0005-additive-only-schema-sync.md), [ADR-0010](0010-local-day-bucketing-python-vs-sql.md), [ADR-0028](0028-weather-location-derived-lazily-from-account-postcode.md).

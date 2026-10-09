# Weather history is backfilled hourly, de-duplicated by (source, time), labelled by source, and a day is complete only if every hour is present

The gas/heating model and dashboard need weather history that matches the whole gas history (daily gas back to 2024-07-24, about 26 months), but `weather_observation` only started filling on 2026-10-06, and it had no uniqueness at all: its only key is an auto-increment `id`, so every write is a plain insert and a re-run (or a backfill overlapping the live job) would silently duplicate rows. We decided: (1) a **unique key on `(source, observed_at)`** plus a keyed upsert (the shared `upsert` only resolves primary-key conflicts), added additively through Schema Sync while the table is nearly empty; (2) a one-off, re-runnable **hourly backfill of every variable we store plus solar** (shortwave radiation, cloud cover, sunshine duration) from 2024-07-24, using Open-Meteo's historical archive for old days and the forecast service's `past_days` (0 to 92) for the recent days the archive has not reached yet; (3) backfilled rows labelled `open-meteo-archive` and live rows `open-meteo`, with readers preferring live where both exist and nothing ever overwriting the other; (4) a London day is **complete** only if every hour of that local day (23, 24 or 25 on daylight-saving days) has a reading from either source, so hours the live job missed are filled by the archive once it catches up; incomplete days are gaps, never partial values; (5) the **daily mean** (not the daily maximum) is the day's temperature, so `weather_forecast` gains a daily mean column.

The decisions above were refined by the dated updates at the end: the unique key also includes the location, and the live job stores hourly data (not the 15-minute `current` sample), re-reading the last 24 hours on every run.

Surprising without context: the archive is a reanalysis (observations blended with weather models and quality-checked later) while live rows are the forecast service's hourly model values, so the two can differ by about a degree on a given hour (not measured for this location); keeping separate labels records provenance and keeps the mixing confined to the boundary between the two series.

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

## Update 2026-10-07: observations record their location

The unique key is `(source, location, observed_at)`, not `(source, observed_at)`. `weather_observation` had no record of which coordinates a reading described, so a change of postcode, or a manual `location` override, would have mixed two places into one series without any sign. Each observation now carries a location key (the Weather Location's coordinates rounded to two decimals, never the postcode); readers and the model use only the current location's key, so a changed location starts a new series; a changed postcode or override is detected by the Weather Location resolution (see the update in [ADR-0028](0028-weather-location-derived-lazily-from-account-postcode.md)). "Local day" in this ADR and the model means the Europe/London time-zone calendar day ([ADR-0010](0010-local-day-bucketing-python-vs-sql.md)), not a place: the temperatures are always those of the postcode's location.

## Update 2026-10-09: the live job stores completed hours from hourly data

The live job first stored Open-Meteo's `current` value, which is a 15-minute sample: it happened to be stamped on the hour only because the job fires early in each hour, and its rain, sunshine and radiation are 15-minute amounts (sunshine at most 900 s), not the hourly totals the archive holds. That would have broken the rule above that a day is complete when every hour is present and that live rows are preferred over archive rows, because a live row could miss the backfill's `:00` key or win with a quarter-hour amount. The live job now requests Open-Meteo's hourly data for the last 24 hours plus the current hour's stamp (`past_hours`; a row stamped H holds the hour that ended at H) and writes each through the keyed upsert, so every live row is stamped on the hour with hourly values that mean the same as the backfill's, and a missed hour (the daily 03:00 restart, an outage) is filled by the next run instead of waiting days for the archive. An hour with no values at all is skipped; a single missing variable is stored as null. Rows written before this change (location key `''`) held 15-minute amounts and are deleted at deploy rather than preferred over the archive; the backfill refills them. The hourly stamp convention (sums cover the hour ending at the stamp) is to be confirmed against the archive when the backfill is built.

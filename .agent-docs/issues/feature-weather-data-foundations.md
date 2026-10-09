# Issues: feature-weather-data-foundations

> Work complete — merged in PR #656. FND-1's existing-table criterion was ticked after the 2026-10-09 deploy (see the spec's deploy record, #657).

## FND-1 · Weather observations cannot be duplicated — [#620](https://github.com/mholubinka1/home-monitoring/issues/620)

**Blocked by**: None

**User stories**: 1, 2, 6

### What to build

Make weather writes safe to repeat. `weather_observation` gets a unique key on `(source, location, observed_at)` (created additively by Schema Sync) and a keyed upsert that updates the existing row when that pair already exists; the live observation job writes through it. The deploy note includes the read-only duplicate check to run before the key is created.

### Acceptance criteria

- [x] Given an observation for an hour already stored for the same source, when it is written again, then exactly one row remains and holds the latest values.
- [x] Given the same hour from two different sources, then both rows exist.
- [x] Given the same hour at two different locations (different location keys), then both rows exist.
- [x] Each observation is stored with the current Weather Location's key (coordinates rounded to two decimals, never the postcode); the column is non-null, and the deploy note deletes the pre-change rows (empty location), which hold 15-minute-sample amounts (see FND-5).
- [x] Given an existing table without the key, when Schema Sync runs, then the key is created (verified against the real MariaDB fixture). Verified on the live MariaDB instead: the 2026-10-09 deploy created the key on the existing 66-row table (see the spec's deploy record). The general existing-table case is covered on the real MariaDB fixture by `libs/common/tests/test_schema_translate_map.py`.
- [x] The live observation job persists through the keyed upsert.
- [x] The deploy note has the read-only query that proves there are no duplicate hours, and the cleanup to run first if there are.

---

## FND-2 · Solar and forecast mean are collected — [#621](https://github.com/mholubinka1/home-monitoring/issues/621)

**Blocked by**: #620

**User stories**: 3, 4

### What to build

The live jobs also collect and store shortwave radiation, cloud cover and sunshine duration on each observation (nullable columns), and the forecast job stores each day's mean temperature (a new `weather_forecast` column). A variable Open-Meteo does not offer for the endpoint in use is stored as null and never fails the whole write.

### Acceptance criteria

- [x] Given a response with the new variables, then they are stored on the observation with the correct units.
- [x] Given a response missing one of them, then the observation is still stored with null for it.
- [x] Given a non-finite new value, then it is rejected like the existing variables.
- [x] Given a forecast response, then each day's mean temperature is stored alongside the maximum.
- [x] Schema Sync adds the new columns to existing tables; the Weather Observation glossary entry is updated.

_Since refined by FND-5: this was delivered against Open-Meteo's 15-minute `current` sample; FND-5 moves the live job to hourly data so these values are hourly amounts._

---

## FND-3 · hive-app stores the Hive working flag — [#622](https://github.com/mholubinka1/home-monitoring/issues/622)

**Blocked by**: None

**User stories**: 5

### What to build

`heating_status` gains a nullable boolean column for the thermostat's own "heating is working" report (the library's current-operation value). hive-app records it on every poll. A missing or unexpected value is stored as null and warned about once per process (later occurrences log at debug). Its meaning is unverified; a short on/off test after deploy decides whether it reflects the boiler.

### Acceptance criteria

- [x] Given a poll where the thermostat reports working, then the row stores true; not working, false.
- [x] Given a poll with no value or an unexpected type, then the row stores null and a warning says so (the first time; later polls log at debug).
- [x] Schema Sync adds the column to the existing table and existing rows are unaffected.
- [x] The existing heating persistence and retrieval tests still pass.
- [x] A note records that the semantics are to be confirmed by an on/off test after deploy.

---

## FND-4 · A changed postcode or location override starts a new weather series — [#652](https://github.com/mholubinka1/home-monitoring/issues/652)

**Blocked by**: #620

**User stories**: 7, 8

### What to build

hive-app's Weather Location resolution records the Account Postcode its cached location was derived from and re-derives it when that postcode differs, which gives a new location key and so a new weather series. An explicit `location` override is never cached; its coordinates give the key, so adding or changing it also starts a new series. Readers use only the current location's key. The postcode itself is never logged.

### Acceptance criteria

- [x] Given the Account Postcode changes, then the next weather run re-derives the location and stores the new location key; observations written before keep their old key.
- [x] Given an explicit `location` is added or changed, then a new location key is used from the next run.
- [x] Given the postcode is unchanged, then the cached location is reused and no geocoding call is made.
- [x] Given a cached IP-derived location, then the existing upgrade to the postcode location still works.
- [x] The postcode never appears in logs or error messages.

---

## FND-5 · The live weather job stores whole, completed hours and fills missed ones — [#655](https://github.com/mholubinka1/home-monitoring/issues/655)

**Blocked by**: #620, #621

**User stories**: 9

### What to build

Replace the live job's use of Open-Meteo's 15-minute `current` sample with its hourly data. Each run requests the last 24 hours plus the current hour's stamp (`past_hours=24`, `forecast_hours=1`, UTC), ignores any hour later than the current one, skips an hour with no values at all, stores a single missing variable as null, and writes every hour through the keyed upsert. Every stored row is stamped on the hour with hourly totals for rain, sunshine and radiation, so live rows mean the same as the backfill's and the daily restart gap is filled by the next run. The `current`-based fetch is removed. The deploy note deletes the pre-change rows (empty `location`).

### Acceptance criteria

- [x] Given an hourly response, then each hour is stored stamped on the hour, with its temperature, humidity, pressure, wind, precipitation, shortwave radiation, cloud cover and sunshine duration.
- [x] Given a response whose latest hour is later than the current hour, then that hour is not stored.
- [x] Given an hour with no values at all, then it is skipped and the other hours are stored.
- [x] Given an hour missing one variable, then it is stored with null for that variable.
- [x] Given a non-finite value in any hour, then the response is rejected like the existing variables.
- [x] Given the same run repeated, then the row count is unchanged and the rows hold the latest values.
- [x] Given an earlier run that missed an hour (the daily restart), then the next run stores that hour.
- [x] The live observation job persists the list through the keyed upsert, with the same source label (`open-meteo`) and location key as before.
- [x] The `current`-based observation fetch and the 15-minute-sample wording (spec, glossary, tests) are removed; the deploy note deletes the pre-change rows.

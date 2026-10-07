# Weather data foundations: safe writes, solar, forecast mean, and the Hive working flag

## Problem Statement

`weather_observation` has no uniqueness: its only key is an auto-increment id, so every write is a plain insert and a re-run, a retry, or a backfill overlapping the live hourly job would silently duplicate hours (and every daily mean computed from them). It also stores only five variables, so the effect of sunshine on gas use cannot be seen, and `weather_forecast` keeps only the daily maximum, although the planned gas model works on daily means. Separately, hive-app records only the thermostat's derived demand (`state`: room below target), not the thermostat's own report of whether the heating is actually running, so that definition of "heating on" cannot yet be checked.

## Solution

Make weather writes safe to repeat, widen what is collected, and start recording the Hive working flag, all additively through Schema Sync, so the backfill and the heating model can build on trustworthy data. This is slice 1 of 5 in the gas/heating work ([ADR-0029](../adr/0029-weather-history-backfilled-hourly-deduplicated-and-labelled-by-source.md), [ADR-0030](../adr/0030-learned-heating-model-and-its-adoption-by-the-cost-forecast.md)).

## User Stories

1. As the account holder, I want an hour of weather stored at most once per source, so that daily averages and charts are never distorted by duplicates.
2. As an operator, I want to re-run or retry any weather write safely, so that a restart, a retry or a later backfill cannot corrupt the data.
3. As the account holder, I want solar radiation, cloud cover and sunshine duration collected from now on, so that the effect of sunshine on gas use can be examined.
4. As the account holder, I want the stored forecast to include each day's mean temperature, so that expected gas can be projected on the same basis the model is fitted on.
5. As the account holder, I want hive-app to record the thermostat's own "heating is running" flag, so that the heating-on definition can later be checked against what the boiler actually did.
6. As an operator, I want a clear pre-deploy check for existing duplicates, so that the new unique key can never make hive-app fail to start.

## Implementation Decisions

- **Unique key.** `weather_observation` gains a unique constraint on `(source, observed_at)`, created additively by Schema Sync ([ADR-0005](../adr/0005-additive-only-schema-sync.md)); it also serves as the `observed_at` index the dashboard queries need.
- **Keyed upsert.** The shared upsert only resolves primary-key conflicts, so add a small keyed variant that updates the existing row when `(source, observed_at)` already exists. The live observation job uses it.
- **New columns (nullable, additive).** On `weather_observation`: shortwave radiation (W/m2), cloud cover (%), sunshine duration (seconds in the hour). On `weather_forecast`: the day's mean temperature (London day). The live jobs request and store them where Open-Meteo offers the variable for the endpoint in use (confirm each variable and its unit at build); a variable that is unavailable is stored as null and never fails the whole observation. The existing finite-number validation applies to the new values.
- **Hive working flag.** `heating_status` gains a nullable boolean column for the thermostat's own "heating working" report (the library exposes it as the current operation). hive-app records it on every poll; a missing or unexpected value is stored as null and logged once. Its meaning is inferred from the name and is unverified; a short on/off test after deploy decides whether it reflects the boiler. No panel depends on it yet.
- **Pre-deploy check.** The unique key can only be created if there are no duplicate hours. The deploy note for this change includes the read-only query that proves there are none (and the cleanup to run first if there are), because Schema Sync never deletes data.
- **Out of the live path.** The live observation keeps its current source label (`open-meteo`) and UTC timestamps.
- **Docs.** Update the Weather Observation glossary entry; ADR-0029 already records the decisions.

## Testing Decisions

- Test external behaviour at the existing hive-app weather seams (observation and forecast persistence, location derivation and refresh scheduling tests) with HTTP mocked at the boundary and the SQLite fixture; test the unique key and keyed upsert against the real MariaDB fixture in `libs/common` (skipped locally without Docker, run by CI on the Pi).
- Good tests: writing the same hour twice leaves one row with the latest values; two sources for the same hour coexist; a missing new variable stores null and does not fail the write; a malformed (non-finite) new value is rejected like the existing ones; the heating poll stores true, false and null for the working flag; Schema Sync adds the new columns and the key to an existing table.
- Prior art: the hive-app weather observation and forecast seam tests, the heating persistence tests, and the Schema Sync tests in `libs/common`.

## Out of Scope

- The history backfill (slice 2), the model (slice 3) and any dashboard (slices 4 and 5).
- Using the working flag in any panel or in the model.
- Deploying to the Pi (a separate, asked-first step) and the on/off test of the working flag.

## Further Notes

- Deploy order: hive-app first (Schema Sync adds the columns and the key on startup), after the duplicate check.
- At the time of writing `weather_observation` holds only a handful of live rows, so adding the key now is cheap; it only gets harder as history accumulates.

# Remove Weather Underground from hive-app

## Problem Statement

hive-app's weather observation path was built around a Weather Underground personal weather station: a `weather_underground` config section, a Weather Underground client, nearest-station discovery and caching, with Open-Meteo as the fallback. Weather Underground only issues API keys to people who own a registered, uploading personal weather station, and the household has no station. The code, config and docs for it are dead weight that suggest a setup step that cannot be completed, and they leave the observation path more complicated than it needs to be (a primary/fallback shape with a "no fallback" contract).

## Solution

Weather Underground is out of scope. hive-app takes both Weather Observation and Weather Forecast from Open-Meteo at the derived Weather Location, with no weather configuration required. All the location work from PR #615 stays: the Account Postcode, postcodes.io geocoding with IP geolocation as the fallback, the `weather_location` cache, and the weather jobs always registering.

## User Stories

1. As the account holder, I want weather collected with no Weather Underground account, key or station, so that there is nothing to set up that I cannot complete.
2. As the account holder, I want observations and forecasts from the same source (Open-Meteo), so that the outdoor-temperature series is consistent.
3. As the account holder, I want the weather location derived from my Octopus postcode as before, so that nothing about location changes.
4. As an operator, I want an Open-Meteo failure to be logged and retried by the job wrapper without a misleading "falling back" message, so that diagnostics are accurate.
5. As a maintainer, I want the Weather Underground client, station cache and config removed, so that the code and docs describe only what exists.

## Implementation Decisions

- Delete the Weather Underground client and its response models, nearest-station discovery, station caching and clearing, and the 5-candidate cap.
- Remove `WeatherUndergroundSettings` and the optional `weather_underground` config section, and drop `station_id` handling. A config file that still contains a `weather_underground` section must not stop hive-app from starting (check how unknown sections are handled and keep it tolerant).
- Drop the `weather_location.station_id` column from the model. The `weather_location` table has never been deployed (PR #615 is unreleased), so no production column exists to leave behind; Schema Sync is additive only (ADR-0005) and never drops columns.
- Collapse `WeatherSource`/`WeatherRetriever` to a single Open-Meteo observation source: no primary/fallback shape, no `None` fallback contract. A failure propagates to the existing job wrapper (retry with backoff, `job_run` failure record). The "Weather Underground fetch failed" / "Primary weather source failed" logging goes with it.
- Weather observation `source` is only `open-meteo`.
- Keep unchanged: Account Postcode table and write, postcodes.io and ipwho.is geocoding, `weather_location` cache and IP-to-postcode upgrade, jobs always registering, postcode privacy handling, explicit `location` config override.
- Docs: amend ADR-0028 with a dated update (Weather Underground dropped and why; what stays), update `context.md` (Weather Observation / Weather Forecast / Weather Location), README and `deployments/hive-app/config.yml.template`. The earlier spec and issues files for the previous branches stay as historical records.

## Testing Decisions

- Behavioural tests at the existing hive-app weather seams, HTTP mocked with `responses`; keep the Open-Meteo-only tests and the location-derivation tests, delete Weather Underground and station-selection tests.
- Add/keep: an Open-Meteo observation failure raises after exactly one Open-Meteo request and logs no fallback message; both jobs register and persist with no weather config; a config containing a stale `weather_underground` section still loads.
- Prior art: the existing wiring and derivation tests from PR #615.

## Out of Scope

- Any other weather provider, or a user-supplied station feed.
- Dropping or migrating anything on the Pi (nothing was deployed).
- Deploying hive-app (a separate, asked-first step after merge) and the Grafana panels #512-#514.

## Further Notes

- Issue #614 (nearest Weather Underground station) becomes moot. #613 stays open for live verification after deploy. Neither is closed by this work without the user's say-so.

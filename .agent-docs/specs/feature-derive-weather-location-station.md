# Derive the weather location and station automatically

## Problem Statement

hive-app's weather jobs only run if the household hand-configures a Weather Underground API key, a station ID and latitude/longitude. On the live Pi none of that is configured, so hive-app logs "will not register weather_observation_refresh", and `weather_observation` and `weather_forecast` are both empty. The planned Grafana panels (#512 indoor/outdoor temperature, #514 gas against outdoor temperature) and the gas cost projection that depends on a Weather Forecast therefore have no data. The household already tells Octopus where it lives, but hive-app cannot use that.

## Solution

hive-app derives the **Weather Location** itself. octopus-app records the **Account Postcode** it already fetches; hive-app geocodes it to coordinates (falling back to IP geolocation), picks the nearest Weather Underground station that is reporting, and falls back to Open-Meteo for observations whenever Weather Underground is not usable. The weather jobs always register. The only thing the household may still supply is a Weather Underground API key; with none, Open-Meteo alone fills `weather_observation`. Explicit `location` and `station_id` config keep working and always win.

## User Stories

1. As the account holder, I want weather data collected without configuring coordinates or a station, so that I do not have to look them up.
2. As the account holder, I want hive-app to use my Octopus account postcode, so that the weather matches where I actually live.
3. As the account holder, I want a fallback to IP geolocation when there is no postcode or it cannot be resolved, so that weather still starts.
4. As the account holder, I want hive-app to pick the nearest Weather Underground station that currently reports, so that I get a real station reading without choosing one.
5. As the account holder, I want Open-Meteo used for observations when I have no Weather Underground key, no reporting station nearby, or a failed read, so that `weather_observation` is never empty for want of a key.
6. As the account holder, I want explicit `location` and `station_id` in config to override derivation, so that an existing setup behaves exactly as before.
7. As the account holder, I want hive-app to start and keep running when octopus-app has not yet written the postcode or a lookup service is down, so that a boot-order race or outage never disables weather until a restart.
8. As the account holder, I want derived coordinates and station cached, so that each hourly poll does not repeat geocoding and station lookups.
9. As the account holder, I want the station re-picked when the cached one stops reporting, so that the series self-heals.
10. As the dashboard user, I want `weather_observation` and `weather_forecast` populated, so that the temperature panels (#512, #514) and the gas projection have data.
11. As an operator, I want a clear log line saying which source and location the weather jobs resolved, and a warning when nothing can be resolved, so that I can diagnose an empty table.

## Implementation Decisions

- **Account Postcode (octopus-app → common):** a one-row `account_postcode` table defined in `common` beside `job_run`, so either app's Schema Sync creates it regardless of start order (ADR-0005, ADR-0028). octopus-app is its only writer, upserting the postcode it already fetches from the Octopus account on startup. hive-app only reads.
- **Weather Location (hive-app):** a `weather_location` table owned by hive-app caching latitude, longitude, location source (config / postcode / IP), the Weather Underground station and `resolved_at`. Resolved lazily on each `weather_observation_refresh` and `weather_forecast_refresh` run: use explicit config if present; else the cache; else derive and store.
- **Coordinates:** Account Postcode geocoded with postcodes.io (free, no key). If there is no postcode row, or postcodes.io does not resolve it, IP geolocation of the Pi's public IP (city-level, accepted as a last resort).
- **Station:** Weather Underground's nearby-stations lookup for the coordinates; choose the nearest station with a current reading, trying at most the 5 nearest (nearest first). If the cached station later returns no reading, clear it and re-pick on the next run; a transient request failure keeps the cache and falls back to Open-Meteo for that run. With an explicit `location` there is no cache row, so the station is rediscovered each run. With no API key or no reporting station, observations come from Open-Meteo.
- **Upgrading an IP-derived location:** a cached `ip` location is re-derived from the Account Postcode once one exists (hive-app booted before octopus-app), and the cached station is cleared with it. If postcodes.io fails the IP row is kept. A later change of postcode is not detected.
- **Privacy:** the postcode is never written to logs, including via postcodes.io failure messages; it is URL-quoted in the request path.
- **Config:** `weather_underground.api_key` stays required only to use Weather Underground; `station_id` and `location` become optional overrides. Config always wins and is never cached over. Existing configs behave as today.
- **Scheduling:** both weather jobs always register. If no coordinates can be resolved on a run, log a warning and retry on the next tick through the existing job wrapper; never crash.
- **Domain docs:** `.agent-docs/context.md` gains Weather Location and Account Postcode; ADR-0028 records the cross-app table and lazy resolution.

## Testing Decisions

- Test external behaviour at the two existing seams, with HTTP mocked at the boundary and a real MariaDB for tables, not internals.
- **hive-app:** the existing weather observation and forecast seams (`test_weather_observation_seam.py`, `test_weather_forecast_seam.py`) plus `test_weather_retrieval.py` and `test_weather_retriever_wiring.py` for registration. Cover: postcode → coordinates; no postcode → IP fallback; postcodes.io failure → IP fallback; nearest reporting station chosen; no key → Open-Meteo; no reporting station → Open-Meteo; WU read fails → Open-Meteo; cached station stops reporting → re-picked; explicit config wins and is not cached; nothing resolvable → warning, no crash, retry next run; jobs register with no weather config.
- **octopus-app:** the existing account seam (`test_account_meter_information_seam.py`) asserting the postcode row is written on startup and overwritten when it changes.
- Prior art: the existing Agile primary/fallback tests for the fallback shape.

## Out of Scope

- Deploying to the Pi, and obtaining a Weather Underground API key.
- Building the Grafana panels (#512, #513, #514).
- Backfilling pre-existing weather data.
- Making Open-Meteo the primary observation source.
- A reverse lookup of the Octopus account from hive-app, or giving hive-app Octopus credentials.

## Further Notes

- Observed on the Pi on 2026-10-06: `weather_observation` and `weather_forecast` both have 0 rows; hive-app logs the "not configured" warning at startup.
- IP geolocation can be wrong behind a VPN; it is only reached when the postcode is unavailable.
- The nearest reporting station can change between runs, so the observation series can switch station; recorded as a consequence in ADR-0028.

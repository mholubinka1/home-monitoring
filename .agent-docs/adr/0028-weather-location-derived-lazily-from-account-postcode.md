# Weather location is derived lazily from the Octopus account postcode, via a cross-app table

Weather Observation and Forecast were silently disabled on the live Pi because `weather_underground` and `location` had to be hand-configured, and `weather_observation` and `weather_forecast` stayed empty. The household's postcode is already known to octopus-app (it fetches the Octopus account). We chose to have octopus-app write it to a one-row `account_postcode` table, defined in `libs/common` like `job_run`, and hive-app derive its Weather Location from it on each run: postcodes.io for coordinates, IP geolocation if there is no postcode or the lookup fails, and Weather Underground's nearby-stations lookup for the station, with Open-Meteo as the observation source whenever Weather Underground is not usable. The result is cached in hive-app's own `weather_location` table. Explicit `location` / `station_id` config always wins and is never cached over.

Surprising without context: hive-app has no Octopus credentials, so the postcode crosses apps through the shared database ([ADR-0022](0022-single-shared-home-monitoring-database.md)) rather than a second Octopus client. The table lives in `common` because Schema Sync only diffs each app's own models ([ADR-0005](0005-additive-only-schema-sync.md)); defined in one app only, a reader that starts first would hit a missing table. Resolution is lazy and retried each run, not done once at startup, so a hive-app that boots before octopus-app, or a lookup that is down at boot, recovers on the next tick instead of disabling the weather jobs until a restart.

## Considered Options

- **Give hive-app Octopus credentials:** self-contained, but duplicates secrets and the account client in a second config.
- **Move weather polling into octopus-app:** puts weather beside the postcode, but restructures shipped work.
- **Resolve once at startup:** simpler, but any boot-order or outage failure disables weather until restart.
- **Pick the station once and store it:** stable, but cannot recover when that station goes offline.
- **Open-Meteo as the primary observation source:** needs no key or station, but is modelled data rather than a station reading.

## Consequences

- IP geolocation is city-level and wrong behind a VPN; it is only the fallback when the postcode is unavailable. A cached IP-derived location is upgraded to the postcode location once an Account Postcode appears (hive-app booted before octopus-app), and the cached station is cleared with it. A later change to the postcode is not detected, because the postcode is not stored in the cache.
- The station is chosen from the 5 nearest returned by Weather Underground, nearest first, to bound the lookups per run; a reporting sixth station is ignored.
- The nearest reporting station can change between runs when the cached one stops returning readings, so the observation series can switch stations. A transient request failure does not clear the cached station; that run falls back to Open-Meteo.
- With an explicit `location` there is no cache row to hold a station, so it is rediscovered on every run and can switch between runs. Accepted: explicit config is the escape hatch, not the default path.

## Update 2026-10-06: Weather Underground dropped

Weather Underground only issues API keys to owners of a registered, uploading personal weather station, and the household has none, so the station-based path could never be switched on. It was removed in #616 before it ever ran in production. Everything above about Weather Underground (the nearby-stations lookup, picking and caching a station, the 5-nearest cap, clearing the station, `station_id` config, the `weather_location.station_id` column, and Open-Meteo as a *fallback*) no longer applies: Weather Observation and Weather Forecast both come from Open-Meteo at the derived Weather Location, with no primary/fallback shape. What this ADR decided about the Account Postcode, the `account_postcode` table in `common`, postcodes.io, the IP-geolocation fallback, lazy resolution, the IP-to-postcode upgrade and the explicit `location` override is unchanged. A station-based source could be reconsidered if the household ever owns a station; it would be a new decision.

## Update 2026-10-07: a changed postcode is now detected

The consequence above that a later change to the postcode is not detected no longer holds. The cached Weather Location records the postcode (or the explicit `location`) it was derived from; when the Account Postcode or the override differs, it is re-derived, which gives a new location key and so a new weather series ([ADR-0029](0029-weather-history-backfilled-hourly-deduplicated-and-labelled-by-source.md)). A cached IP-derived location is still upgraded to the postcode location as before.

# Issues: feature-derive-weather-location-station

> Work complete — [PR #615](https://github.com/mholubinka1/home-monitoring/pull/615) ready to merge.
> #612 closes with the PR. #613 and #614 stay open until verified live after
> deploy: with a Weather Underground API key in the Pi's hive-app config,
> `weather_observation`, `weather_forecast` and `weather_location` fill. The
> Weather Underground nearby-stations response shape is mocked, not yet
> checked against the live API.

## octopus-app: record the Account Postcode in a shared table — [#612](https://github.com/mholubinka1/home-monitoring/issues/612)

**Blocked by**: None

**User stories**: 2, 7

### What to build

octopus-app records the Octopus account's property postcode (the Account
Postcode) in a one-row `account_postcode` table defined in `libs/common` beside
`job_run`, so either app's Schema Sync creates it regardless of start order.
octopus-app is the only writer: it upserts the postcode it already fetches from
the Octopus account on startup. hive-app only reads it (later slices).

### Acceptance criteria

- [x] Given the Octopus account has a postcode, when octopus-app starts, then
      `account_postcode` holds that postcode.
- [x] Given the stored postcode differs from the account's, when octopus-app
      starts, then the row is overwritten (still one row).
- [x] Given hive-app starts before octopus-app, then its Schema Sync still
      creates `account_postcode`.
- [x] Verified at the `MonitoringClient` startup seam (new
      `test_account_postcode_persistence.py`, SQLite fixture DB) and against a
      real MariaDB (`libs/common/tests/test_account_postcode.py`, Docker-gated,
      so it runs on CI but is skipped where Docker is unavailable).

---

## hive-app: derive the Weather Location and always register the weather jobs — [#613](https://github.com/mholubinka1/home-monitoring/issues/613)

**Blocked by**: #612 (the postcode path; the IP and config paths work without it)

**User stories**: 1, 2, 3, 6, 7, 8, 10, 11

### What to build

hive-app derives its Weather Location instead of requiring `location` config,
and the weather jobs always register. Coordinates come from the Account
Postcode geocoded via postcodes.io; with no postcode or a failed lookup, IP
geolocation. The result is cached in a hive-app-owned `weather_location` table
and resolved lazily each run. Explicit `location` config always wins and is
never cached over. With no Weather Underground configuration, observations come
from Open-Meteo, so `weather_observation` and `weather_forecast` fill with no
config at all.

### Acceptance criteria

- [x] Given an Account Postcode, then coordinates come from postcodes.io and are
      cached in `weather_location`.
- [x] Given no postcode, or a postcodes.io failure, then IP geolocation supplies
      the coordinates.
- [x] Given explicit `location` config, then it is used and nothing is cached
      over it.
- [x] Given nothing resolves, then a warning is logged, hive-app does not crash,
      and the next tick retries.
- [x] Given no weather config at all, then both weather jobs register and
      `weather_observation` is filled from Open-Meteo.
- [x] A log line names the resolved source and location.
- [x] Verified at the existing weather observation/forecast seams, with HTTP
      mocked at the boundary.

---

## hive-app: pick the nearest reporting Weather Underground station — [#614](https://github.com/mholubinka1/home-monitoring/issues/614)

**Blocked by**: #613

**User stories**: 4, 5, 9

### What to build

With a Weather Underground API key configured, hive-app picks the nearest
station with a current reading via Weather Underground's nearby-stations lookup
for the Weather Location, caches it, and uses it for observations. With no key,
no reporting station, or a failed read, observations come from Open-Meteo. If
the cached station stops reporting it is cleared and re-picked on the next run.
An explicit `station_id` in config always wins.

### Acceptance criteria

- [x] Given an API key and coordinates, then the nearest station with a current
      reading is chosen and cached.
- [x] Given no API key, no reporting station, or a failed Weather Underground
      read, then the observation comes from Open-Meteo.
- [x] Given the cached station stops reporting, then it is re-picked on the next
      run.
- [x] Given explicit `station_id` config, then it wins and is not cached over.
- [x] Verified at the existing weather observation seam, with HTTP mocked at the
      boundary.

# Issues: feature/hive-app-heating-weather

> Work complete on #506 — [PR #525](https://github.com/mholubinka1/home-monitoring/pull/525) ready to merge. (The other issues in this file are separate, still-open slices of the same epic — not implemented by this PR.)

## hive-app: skeleton, Hive auth, and heating status polling — [#506](https://github.com/mholubinka1/home-monitoring/issues/506)

**Blocked by**: None

**User stories**: 6

### What to build

The walking skeleton for hive-app: a config-driven entrypoint with a scheduler loop, MariaDB persistence via the existing Schema Sync convention, and `job_run` logging — the same shape `app/main.py`/`app/data/mysql/client.py` already establish for octopus-app. On top of that skeleton, the first real capability: authenticate to Hive via the community `apyhiveapi` library's Cognito-SRP flow, persist the resulting refresh token and device keys to a new `hive_auth_state` table so a restart resumes via token/device refresh rather than a full interactive login, and poll heating status (current/target temperature, mode, state, boost, and the now/next/later schedule as JSON) every 120 seconds into a new `heating_status` table via a `HiveSource` protocol (mirroring `PricingSource`) and a `HeatingRetriever` (mirroring `ConsumptionRetriever`/`PricingRetriever`).

### Acceptance criteria

- [x] Given valid Hive credentials in config, when hive-app starts for the first time, then it completes an interactive Cognito login, persists a `hive_auth_state` row (refresh token, device group key, device key, device password), and the `heating_refresh` job begins running on a 120-second interval.
- [x] Given a `hive_auth_state` row already exists from a prior run, when hive-app restarts, then it resumes via token/device refresh — no interactive login is attempted.
- [x] Given a successful heating poll, when it completes, then a `heating_status` row is written with current temp, target temp, mode, state, boost fields, and the schedule as JSON, and a `job_run` row records success for `heating_refresh`.
- [x] Given a transient poll failure (e.g. a network error, not an auth failure), when it occurs, then it's recorded as a `job_run` failure with retry-with-backoff, mirroring `_schedule_refresh_job`'s existing behaviour — no crash, no special handling beyond what every other job already gets.
- [x] `HeatingRetriever` is tested against a fake `HiveSource` (Protocol implementation, no live or mocked Cognito flow) — mirrors `test_pricing_retrieval.py`'s seam for `PricingSource`.

> The two `hive_auth_state` criteria above describe this issue's original MariaDB-table design, which shipped and was reviewed as part of this same PR. That design was superseded before merge — see [ADR-0019](../adr/0019-file-based-hive-auth-state-storage.md) and the spec's "`hive_auth_state` file-based storage" section — `hive_auth_state` is now a JSON file in hive-app's `/config` volume, not a MariaDB row. Left unedited above as the historical record of what this issue originally asked for; the criteria remain satisfied in spirit (persist-then-resume-via-refresh), just not via the literal mechanism described.

---

## hive-app: active alert on unrecoverable Hive re-auth — [#509](https://github.com/mholubinka1/home-monitoring/issues/509)

**Blocked by**: #506

**User stories**: 7, 8

> Work complete — [PR #526](https://github.com/mholubinka1/home-monitoring/pull/526) ready to merge. (The other issues in this file are separate, still-open slices of the same epic — not implemented by this PR.)

### What to build

A narrowly-scoped `notify_reauth_required()` helper, called only from the heating-poll job's failure path when Cognito no longer recognizes the remembered device and a live SMS 2FA code is needed (a genuinely unrecoverable state for a headless service — see `.agent-docs/research/hive-api-access-approach.md`). It POSTs to a configured ntfy.sh topic URL. Every other hive-app failure — weather source outages, ordinary transient Hive poll failures — continues to rely solely on the existing `job_run`/Grafana-staleness pattern; this helper is not wired into generic failure handling (see [ADR-0018](../adr/0018-ntfy-for-hive-reauth-alerting.md)).

### Acceptance criteria

- [x] Given a `HiveSource` poll raises the specific "device not recognized, needs live SMS" error, when `HeatingRetriever` handles that failure, then `notify_reauth_required()` is called and POSTs to the configured ntfy.sh topic.
- [x] Given a `HiveSource` poll raises any other error (network failure, transient API error), when `HeatingRetriever` handles that failure, then `notify_reauth_required()` is **not** called — it still records a `job_run` failure as normal.
- [x] The ntfy.sh POST is mocked at the HTTP boundary (`responses`-style) in tests, matching this repo's existing seam conventions.

---

## hive-app: weather observation polling (Weather Underground + Open-Meteo fallback) — [#508](https://github.com/mholubinka1/home-monitoring/issues/508)

> Work complete — [PR #534](https://github.com/mholubinka1/home-monitoring/pull/534) ready to merge. (The other issues in this file are separate, still-open slices of the same epic — not implemented by this PR.)

**Blocked by**: #506

**User stories**: 1, 5

### What to build

A `WeatherSource` protocol (same shape as `HiveSource`/`PricingSource`) with a `fetch_current_observation()`/`persist_current_observation()` verb pair, and two concrete clients: a Weather Underground client (station `IBECKE4`) as primary, Open-Meteo as fallback on failure — mirroring `PricingRetriever`'s existing Agile Predict → x2r.uk primary/fallback pattern. Persists to a new `weather_observation` table (source, observed_at, temp, humidity, pressure, wind_speed, precipitation) every 60 minutes via a `weather_observation_refresh` job, reusing hive-app's scheduler/`job_run` scaffolding from #506.

### Acceptance criteria

- [x] Given Weather Underground's API responds successfully, when the `weather_observation_refresh` job runs, then a `weather_observation` row is written with `source = 'wunderground'` and the observed fields.
- [x] Given Weather Underground's API call fails, when the job runs, then Open-Meteo is called as fallback and a `weather_observation` row is written with `source = 'open-meteo'` — the job still succeeds (recorded as `job_run` success), not a failure.
- [x] Given both sources fail, when the job runs, then it's recorded as a `job_run` failure with retry-with-backoff — no ntfy alert (out of scope for this failure path, see #509).
- [x] Both clients are tested with HTTP-boundary mocking via `responses`, matching `test_consumption_seam.py`'s pattern — real client code runs against a mocked response, result asserted after round-tripping through a real (SQLite-backed) `MariaDBClient`.

---

## hive-app: weather forecast polling (Open-Meteo) — [#510](https://github.com/mholubinka1/home-monitoring/issues/510)

> Work complete — [PR #535](https://github.com/mholubinka1/home-monitoring/pull/535) ready to merge. (The other issues in this file are separate, still-open slices of the same epic — not implemented by this PR.)

**Blocked by**: #508

**User stories**: 4, 5

### What to build

Extends `WeatherSource` with a `fetch_forecast()`/`persist_forecast()` verb pair, fetching Open-Meteo's forecast endpoint (upcoming days' max temperature) hourly via a `weather_forecast_refresh` job. Persists to a new `weather_forecast` table (source, target_date, max_temp, fetched_at). No fallback source for the forecast itself — day-one scope keeps this to Open-Meteo only.

### Acceptance criteria

- [x] Given Open-Meteo's forecast endpoint responds successfully, when the `weather_forecast_refresh` job runs, then `weather_forecast` rows are written, one per upcoming day, each with that day's predicted max temperature.
- [x] Given the forecast call fails, when the job runs, then it's recorded as a `job_run` failure with retry-with-backoff — same as every other job, no fallback source attempted.
- [x] Re-running the job for a day already present in `weather_forecast` upserts (updates) that day's figure rather than duplicating a row — mirrors the upsert pattern `write_agile_forecast`/`write_product_rate` already use.
- [x] Tested with HTTP-boundary mocking via `responses`, same seam as the observation client.

---

> Work complete — PR #522 ready to merge. (The other issues in this file are separate, still-open slices of the same epic — not implemented by this PR.)

## octopus-app: generalize cost_forecast to gas (average-based projection) — [#507](https://github.com/mholubinka1/home-monitoring/issues/507)

**Blocked by**: None

**User stories**: 3

### What to build

`cost_forecast.py` is currently implicitly electricity-only (`_current_electricity_agreement` hard-codes `Energy.electricity`). Add an `energy` column to the existing `cost_forecast` table ([ADR-0016](../adr/0016-energy-column-on-cost-forecast.md)), generalize agreement lookup to accept an `Energy` parameter, and run `CostForecastRetriever.refresh()` once per energy type, writing a separate `(billing_period, energy)` row for each. Gas's `actual_cost_to_date` reuses the existing `consumption ⋈ agreement ⋈ product_rate` join, filtered to `energy = 'G'` — already-synced data, just never read by this module before. Gas's `projected_total_cost` uses the **existing** average-recent-consumption projection method unchanged (`_project_remaining_cost`'s non-Agile branch already generalizes to any energy type — gas has no Agile tariff, so this path needs no new logic, only the generalized `Energy` parameter threaded through). This ships a fully working, if not weather-aware yet, gas cost forecast with minimal new logic; the weather-aware upgrade is a separate, later slice (#511).

### Acceptance criteria

- [x] Given the `cost_forecast` table predates this change, when Schema Sync runs, then it adds the new `energy` column — pre-existing rows are left as-is (Schema Sync never backfills; the one-time manual `UPDATE cost_forecast SET energy = 'E'` on the live production database is a deployment step, not app behaviour, and is called out explicitly wherever this ships to production).
- [x] Given a gas meter with synced `agreement`/`product_rate` data, when `CostForecastRetriever.refresh()` runs, then a `cost_forecast` row with `energy = 'G'` is written, with `actual_cost_to_date` matching a hand-computed sum from `consumption ⋈ agreement ⋈ product_rate`.
- [x] Given the same gas data, when `projected_total_cost` is computed, then it matches the existing average-recent-consumption method's result (same formula currently used for non-Agile electricity) — proving no behavioural regression for electricity and correct reuse for gas.
- [x] The existing electricity `cost_forecast` row/tests are unaffected — `energy = 'E'` continues to compute exactly as before.
- [x] Extends `test_cost_forecast_*.py`'s existing seeded-SQLite pattern with gas fixtures.

---

## octopus-app: weather-aware gas cost projection — [#511](https://github.com/mholubinka1/home-monitoring/issues/511)

> Work complete — [PR #537](https://github.com/mholubinka1/home-monitoring/pull/537) ready to merge.

**Blocked by**: #510, #507

**User stories**: 4

### What to build

Replaces gas's flat average-based `projected_total_cost` (from #507) with a live-computed (nothing persisted) linear regression: daily gas kWh (`daily_consumption_summary`, `energy = 'G'`) against that day's max outdoor temperature (`weather_observation`, aggregated to a daily max) over a trailing historical window, recomputed fresh on every `cost_forecast_refresh` run ([ADR-0010](../adr/0010-local-day-bucketing-python-vs-sql.md)'s existing precedent for Python over stored derived state). For each remaining day in the billing period, the regression predicts a kWh figure from that day's `weather_forecast.max_temp`, converted to £ via the same live `product_rate` gas already has synced. Structurally closer to the existing Agile variable-cost branch (per-day/per-reading iteration against external forecast data) than the flat non-Agile branch it replaces for gas specifically — electricity's projection is untouched.

### Acceptance criteria

- [x] Given historical `daily_consumption_summary`(gas) and `weather_observation` data showing a temperature/consumption relationship, when the regression is computed, then its coefficients reflect that relationship (higher consumption on colder days).
- [x] Given a `weather_forecast` showing a colder-than-recent-average upcoming period, when gas's `projected_total_cost` is computed, then it is higher than the flat average-based projection would have produced for the same remaining days — and vice versa for a forecast milder period.
- [x] Given no `weather_forecast` data exists for some or all remaining days (e.g. hive-app hasn't run yet, or Open-Meteo has been down), when the projection runs, then it falls back to #507's average-based method for those days rather than raising or silently omitting them.
- [x] Electricity's `projected_total_cost` computation and result are byte-for-byte unaffected.
- [x] Test: a hand-seeded cold-forecast day produces a higher projected total than an otherwise-identical mild-forecast day, proving the regression is actually applied, not just an average.

---

## Grafana: indoor/outdoor temperature panel — [#512](https://github.com/mholubinka1/home-monitoring/issues/512)

**Blocked by**: #506, #508

**User stories**: 1

### What to build

A new panel query in `grafana/mariadb/queries.md` (no dashboard exists to wire it into yet — same standing situation as `feature-yearly-consumption-comparison.md`'s panels) overlaying `heating_status.current_temp` and `weather_observation.temp` as two time series on one chart.

### Acceptance criteria

- [ ] Query documented in `grafana/mariadb/queries.md`, runnable against a real MariaDB instance with both tables populated, returning both series aligned on time.
- [ ] No automated test for the panel SQL itself — verified against a real MariaDB instance when the dashboard feature itself is implemented, matching the standing decision already made for other Grafana query additions.

---

## Grafana: gas consumption with heating-ON shading — [#513](https://github.com/mholubinka1/home-monitoring/issues/513)

**Blocked by**: #506

**User stories**: 2

### What to build

A new panel query showing half-hourly gas `consumption` (`energy = 'G'`) as the base series, with `heating_status` periods where heating was actively on shaded differently. Requires confirming, against real polled `heating_status` data, exactly which `mode`/`state`/`heat_on_demand` combination means "actively heating" — the spec explicitly flags this as unconfirmed pending real payloads (`.agent-docs/research/hive-api-access-approach.md` only confirms the fields exist, not their exact semantics).

### Acceptance criteria

- [ ] The "heating is active" predicate is confirmed against real `heating_status` rows (from #506 running against the actual Hive account) before being encoded into the query — not guessed from documentation alone.
- [ ] Query documented in `grafana/mariadb/queries.md`, returning gas consumption plus shaded regions matching actual heating-on periods for a sample time range.
- [ ] No automated test for the panel SQL itself, same standing decision as the temperature-overlay panel.

---

## Grafana: gas-vs-outdoor-temperature correlation view — [#514](https://github.com/mholubinka1/home-monitoring/issues/514)

**Blocked by**: #508

**User stories**: 5

### What to build

A new panel query (scatter or similar) joining `daily_consumption_summary` (`energy = 'G'`) against a daily max of `weather_observation.temp`, letting the account holder visually judge whether a given day's gas usage was normal for the weather.

### Acceptance criteria

- [ ] Query documented in `grafana/mariadb/queries.md`, runnable against a real MariaDB instance with both tables populated.
- [ ] No automated test for the panel SQL itself, same standing decision as the other Grafana panels in this set.

---

# Octopus Monitoring

A scheduled worker that polls the Octopus Energy API for a UK household's electricity and gas consumption, normalizes it, and persists it to MariaDB for downstream visualization (e.g. Grafana).

## Language

### Octopus Energy Domain

**MPAN**:
Meter Point Administration Number — the unique identifier for an electricity meter point.
_Avoid_: electricity meter ID, meter number

**MPRN**:
Meter Point Reference Number — the unique identifier for a gas meter point.
_Avoid_: gas meter ID, meter number

**Meter Point**:
The abstract connection point for a fuel supply, represented in code as `Electricity` or `Gas`, both subclasses of `Meter`.
_Avoid_: meter, supply point

**Agreement**:
A tariff contract period held against a meter, carrying a tariff code, product code, validity dates, and price history.
_Avoid_: contract, plan

**Tariff Code / Product Code**:
Octopus's identifiers for a pricing plan; the product code is derived from the tariff code by regex.
_Avoid_: plan ID, rate code

**Tariff Type**:
The classification of a pricing plan — `variable`, `economy7`, `agile`, `fixed`, or `prepay`. Only `economy7` and `agile` are currently detected in code.
_Avoid_: plan type, rate type

**Agile**:
Octopus's half-hourly dynamic electricity pricing tariff, detected via tariff code containing `AGILE`. Its rates are fetched and stored through the same generic path as every other product (see `PricingRetriever`); a dedicated Agile cost forecast feature is still unbuilt (see Cost and Forecasting below).
_Avoid_: dynamic tariff, agile octopus

**Standing Charge**:
The fixed daily charge component of a tariff. Modelled in `Price`/`Rate` and persisted per product/region in the `product_rate` table by `PricingRetriever`.
_Avoid_: daily charge, base fee

**Unit Rate**:
The per-kWh price component of a tariff. Same storage path as standing charge.
_Avoid_: price per unit, rate

**Consumption**:
A single metered usage record for a time interval: raw value, unit, and an estimated kWh figure.
_Avoid_: usage, reading

**Estimated kWh (`est_kwh`)**:
Consumption normalized to kWh. For gas this applies a volume correction factor (1.02264) and calorific value (39.5) to convert from m³.
_Avoid_: normalized usage, kwh value

**Region Code / GSP**:
The Grid Supply Point code for a geographic distribution zone, looked up from postcode; required to select region-specific tariff pricing.
_Avoid_: zone, area code

**Account**:
An Octopus Energy account — holds an account number, address, and postcode, and can have multiple meters.
_Avoid_: customer, user

### Data Storage

**`home_monitoring` database** (formerly `octopus`):
The single shared MariaDB database for both `octopus-app` and `hive-app`, on the same MariaDB instance. Holds `octopus-app`'s tables (`consumption`, `agreement`, `product`, `product_rate`, `daily_consumption_summary`, `agile_forecast`, `cost_forecast`) and `hive-app`'s (`heating_status`, `weather_observation`, `weather_forecast`), plus the cross-app `job_run` table owned by `common`. Renamed from `octopus` to `home_monitoring` during the live Pi cutover on 2026-10-01 ([ADR-0022](adr/0022-single-shared-home-monitoring-database.md), [#547](https://github.com/mholubinka1/home-monitoring/issues/547)), via `scripts/rename_database.sql` run against the real instance — all 11 tables' row counts verified identical before and after. Every SQLAlchemy model still declares the literal `schema="octopus"` in its `__table_args__`; that is intentional and permanent, not a missed rename — [ADR-0025](adr/0025-schema-translate-map-for-config-driven-mariadb-schema.md) makes it a fixed `schema_translate_map` source key, translated to the configured `settings.database` value (`home_monitoring`) at query-execution time, so a future rename needs only a config change. The old, empty `octopus` database was dropped on the Pi on 2026-10-04 ([#589](https://github.com/mholubinka1/home-monitoring/issues/589)); the old Docker Hub repo `mholubinka1/octopus-monitoring` is archived, deliberately not deleted.
_Avoid_: octopus database (the old name, pre-2026-10-01; the empty database was dropped 2026-10-04), the database, mysql db

**schema** (as in `schema_translate_map`, `__table_args__ = {"schema": ...}`):
This codebase's SQLAlchemy/MySQL usage of "schema" is a synonym for **database** (MySQL/MariaDB, unlike Postgres, has no schema/database distinction) — it is not related to, and should not be confused with, **Schema Sync** below, which is a distinct table-reconciliation mechanism named before this synonymy became load-bearing. A model's `schema=` value and a connection's target database are the same kind of thing; see the `home_monitoring` database entry above for why the literal stays `"octopus"` everywhere rather than being renamed.
_Avoid_: schema (unqualified, when the reader might mean Schema Sync instead) — prefer "the `schema=` value" or "the database" to disambiguate

**Schema Sync**:
The additive-only schema reconciliation each app's MariaDB client runs automatically on startup — creates any table missing from the live database, adds any column missing from an existing table, and creates any index missing from an existing table, diffed against that app's own SQLAlchemy models. Never drops or alters an existing column or index; that stays a deliberate manual action. The mechanism itself (engine/session plumbing, the diff-and-create logic) lives in `common` and is shared, but each app's Schema Sync run only ever diffs against its own models — the database is shared, not the schema-sync run. See [ADR-0005](adr/0005-additive-only-schema-sync.md) and [ADR-0022](adr/0022-single-shared-home-monitoring-database.md).
_Avoid_: migration, schema migration (this project deliberately has no versioned migration tool)

**InfluxDB (legacy)**:
A former time-series store, described historically in the README; its implementation (`app/_deprecated/`) has been removed entirely — MariaDB is, and has been, the only active sink.
_Avoid_: the time-series DB (when referring to the current system)

### Scheduling and Retrieval

**Startup Backfill**:
The historical consumption retrieval run on every process start, bounded by `retention_days` (default 400) — not one-time: `ConsumptionRetriever`'s last-retrieved watermark is in-memory only, so this re-runs in full on every restart, not just the first ever run. The same full-window call is also made periodically by the **Consumption Backfill Job**, so it is no longer restart-only.
_Avoid_: initial sync, bootstrap, one-time sync

**Consumption Backfill Job**:
A daily job (`consumption_backfill`, `DAILY_JOB_TIME`) that re-runs the Startup Backfill's full-retention-window `ConsumptionRetriever.retrieve()` call, so a day that fell behind the Refresh Loop's cursor, or a permanent gap Octopus never backfills, self-heals on a fixed cadence rather than only at process restart. See [ADR-0011](adr/0011-periodic-consumption-backfill-full-window-reuse.md).
_Avoid_: gap filler, consumption repair job

**Refresh Loop**:
The recurring poll of the Octopus API, driven by the `schedule` library on the configured `refresh_interval_hours`.
_Avoid_: polling loop, cron job

**`ConsumptionRetriever`**:
Orchestrates paginated consumption retrieval from Octopus and writes it to MariaDB, tracking the last-retrieved timestamp per energy type.
_Avoid_: consumption service

**`PricingRetriever`**:
Orchestrates syncing agreements, the product catalogue, the account's own product rates, and comparison rates for every other available product, writing all of it to MariaDB via `PricingSource`.
_Avoid_: pricing service

**`MonitoringClient`**:
The top-level facade wiring the Octopus API client and MariaDB client together; holds account/meter state for a run.
_Avoid_: app client, main client

### Cost and Forecasting

**Billing Period**:
The tariff charge cycle for an account — the date range Octopus actually bills consumption against, distinct from Octopus's own account statement/ledger window (previous-balance-date to new-balance-date), which runs exactly one day later on both ends. Fetched from Octopus's GraphQL "Kraken" API (`account.billingOptions`), authenticated by exchanging the account's existing REST API key for a short-lived JWT via `obtainKrakenToken` — not available via the REST v1 API this app otherwise uses. Kraken's `currentBillingPeriodStartDate`/`currentBillingPeriodEndDate` fields report the statement window, not the tariff window, so `BillingPeriod.from_billing_options` shifts them back a day to recover the true tariff dates. For accounts on flexible billing (`isFixed: false`, no `currentBillingPeriodEndDate` from Octopus — the case for this account), the period end is derived from the (already-shifted) period start plus one calendar month, same day-of-month, minus one further day to match the account's actual cycle shape (`[day X, day X−1 of next month]`, not `[day X, day X]`), clamped to the last valid day if that day doesn't exist in the target month. See `.agent-docs/research/octopus-billing-period-api.md`.
_Avoid_: billing cycle, invoice period

**Product / Product Rate**:
`Product` is Octopus's public catalogue entry for a tariff plan, distinct from `Agreement` (the account's actual contract). `Product Rate` is a product's unit rate and standing charge for a region and time period — stored uniformly for every product, including whichever one the account is actually on, so actual cost and the price-curve panel read from the same table.
_Avoid_: tariff (when referring to the public catalogue rather than the account's own agreement)

**Actual Cost**:
Cost computed directly from real consumption × the real rates actually charged (`consumption` ⋈ `agreement` ⋈ `product_rate`) — covers "yesterday's cost" (no billing-period dependency) and "this billing period's cost so far" (needs the billing period start, so computed and persisted by the app rather than a pure live query).
_Avoid_: spend, actual spend

**Cost Forecast**:
A projection of total cost for the current billing period, built from actual cost to date plus a forecast for the remaining days: future consumption estimated as the average daily usage of the billing period so far, and future price read from whatever the Agile Forecast Refresh job most recently persisted, tiled (the last 7 forecast days repeated in sequence) for any remaining days beyond that stored horizon. Does not fetch a forecast itself — see Agile Forecast Refresh. Refreshed hourly; when a past gap day's published rates are missing or incomplete it is still written, with its rates estimated and flagged (see Estimated Rates).
_Avoid_: price forecast (that term refers to the underlying Agile price data, not the derived cost projection)

**Estimated Rates**:
The rates used to price a past gap day whose published rates are missing or incomplete: the time-weighted average unit rate of that day's own published segments, or, with none, the unit rate and standing charge of the nearest earlier fully published day under the gap day's own agreement. At most 3 days per energy per refresh; beyond that the refresh fails. The Cost Forecast row records it in `rates_estimated` and `estimated_days`, which flag only rate estimation, not kWh-estimated gap-filled days. See `.agent-docs/adr/0026-missing-published-rates-on-a-past-gap-day-are-estimated-and-flagged.md`.
_Avoid_: fallback rates, guessed rates

**Agile Predict**:
A third-party public service (`agilepredict.com`, backed by the same Fly.io app historically documented at `prices.fly.dev` — that domain's `/v2/<region>/` path now serves the HTML frontend, not JSON) providing a hard-capped 14-day-ahead Agile price forecast per GSP region via `GET https://agilepredict.com/api/{region}/`, no authentication required. The primary source for the Agile Forecast Refresh job; consumed as an external API rather than reimplemented in-house — see `.agent-docs/adr/0002-agile-predict-forecast-dependency.md`.
_Avoid_: the forecast API, prediction service

**x2r.uk**:
A second third-party hobby forecast service (`api.x2r.uk`, independent hosting from Agile Predict's Fly.io deployment), providing a ~14-day-ahead Agile price forecast per GSP region via `GET https://api.x2r.uk/agile/{region}`. Used as the Agile Forecast Refresh job's fallback source when Agile Predict fails — same region-code format, different response shape (nested `prices.forecast`/`day_ahead`/`actual`, `date`/`price` fields rather than Agile Predict's flat `date_time`/`agile_pred` list), so it has its own client and its own mapping into `AgileForecastReading`. See `.agent-docs/adr/0002-agile-predict-forecast-dependency.md`.
_Avoid_: the fallback API, backup forecast

**Agile Forecast Refresh**:
The hourly job that fetches Agile price forecast readings (Agile Predict primarily, falling back to x2r.uk on failure) and upserts them into `agile_forecast`. Runs on its own cadence, decoupled from the Cost Forecast job, so an outage of one or both forecast sources no longer blocks the same-day cost projection from recomputing off whatever forecast data is already stored.
_Avoid_: forecast sync, price forecast job

**Job Run**:
A logged execution record (job name, status, timestamp) for each scheduled job — consumption refresh, pricing refresh, Agile Forecast Refresh, cost forecast refresh — used to drive the dashboard's health/staleness panel.
_Avoid_: job log, task run

**Retention Window**:
The 45-day period after which raw consumption and product-rate rows are pruned by `DataPruner` (`apps/octopus-app/octopus_app/data/pruning.py`), a weekly job that runs Monday 04:00 immediately after the consumption-summary job, and only if that summary job's _this-cycle_ run succeeded — so raw data is never deleted before it has been safely rolled up. `agreement` rows are never pruned. `retention_days` (45) also bounds the Startup Backfill's lookback. Derived/aggregated results (e.g. `cost_forecast`, `daily_consumption_summary`) are exempt from pruning. Was briefly widened to 400 days as a stopgap to carry raw history for a not-yet-built summarization pass, then reverted to 45 once `feature/yearly-consumption-comparison` shipped a dedicated backfill (see Consumption Summary) that no longer depends on raw-data retention. See `.agent-docs/adr/0003-90-day-data-retention.md`.
_Avoid_: data expiry, TTL

**Consumption Summary**:
The `daily_consumption_summary` table (`energy`, `date`, `total_kwh`, composite primary key) — a pruning-exempt daily aggregate of raw `consumption`, populated two ways: a weekly `update_consumption_summary` job (Monday 03:00, re-summarizes the trailing 14 days plus any never-yet-summarized older days, to absorb upstream Octopus corrections to estimated readings), and a one-time startup backfill (`yearly_comparison_backfill`, gated on `job_run` history) that fetches ~2 years directly from Octopus's API without ever writing to raw `consumption`. Backs the Yearly Comparison panels so they remain correct regardless of the raw retention window. Both paths bucket by Europe/London local day, and consumption is requested newest-first so paging returns each interval once ([ADR-0027](adr/0027-consumption-requests-use-descending-order.md)); days Octopus has no readings for stay absent.
_Avoid_: daily total, consumption rollup

**Yearly Comparison**:
The pair of Grafana panels (monthly total consumption over the trailing 12 months, and a week-over-week year-on-year % change by ISO week number, both split by energy) reading from `daily_consumption_summary`. ISO week numbering (MariaDB `YEARWEEK(date, 3)`) is used specifically to avoid the "week 0" ambiguity of calendar-week numbering and to avoid misattributing early-January/late-December boundary dates to the wrong week-year; an orphan week 53 (a year with no matching week 53 a year prior) falls back to comparing against that prior year's week 52. The weekly panel only compares ISO weeks with all 7 days present (`HAVING COUNT(*) = 7`) — the current, still-in-progress week and the oldest weeks near the one-time 2-year backfill's non-week-aligned boundary can otherwise be short, understating totals and skewing the % change.
_Avoid_: annual comparison, YoY chart

**Cheap Window**:
The cheapest contiguous block of a given duration (30min/1h/2h/3h/4h/6h) within today's or tomorrow's Agile half-hourly rates, computed live at query time rather than stored.
_Avoid_: best time to use power, price dip

**Day Completeness**:
A local calendar day has 48 half-hourly `consumption` rows once Octopus's settlement lag has fully caught up — confirmed to take more than 24 hours in practice (a day can sit at 2/48 or 0/48 rows a full day after it ends) — except the two UK clock-change days each year, which are 46 (spring-forward) or 50 (fall-back) rows. Any query grouping by day must guard on the expected row count for that specific day for strictly past days before treating that day's total as final, to avoid presenting a lag-truncated day as a genuinely low-cost/low-usage one. The current, still-in-progress day is exempt from this guard — it's expected to be partial. See [ADR-0009](adr/0009-day-completeness-guard-standing-charge-fallback.md).
_Avoid_: data lag, settlement delay (when referring to the guard itself, not the underlying cause)

**Local Day**:
The Europe/London calendar day used for every day-bucketed cost/consumption figure — `cost_forecast.py`, the weekly consumption-summarization job, and every Grafana panel that groups by day or hour. `consumption.period_from`/`period_to` are stored in UTC, so bucketing by day requires converting to local time first: `zoneinfo.ZoneInfo("Europe/London")` in app code, `CONVERT_TZ(period_from, 'UTC', 'Europe/London')` in the standalone Grafana reference queries (which have no SQLite-compatibility constraint, unlike the app's own test suite). See [ADR-0010](adr/0010-local-day-bucketing-python-vs-sql.md).
_Avoid_: UTC day, calendar day (when the raw UTC date is meant instead of the app's local-day convention)

### Home Monitoring Restructure (in progress)

**Home Monitoring**:
This repo was renamed from `octopus-monitoring` now that it hosts more than one data-gathering container. Encompasses `octopus-app` and `hive-app`, sharing one MariaDB instance/database (`home_monitoring` — see that term's entry) for downstream visualization (Grafana). Repo layout, already landed: `apps/` (deployable containers only — `octopus-app`, `hive-app`), `libs/` (`common`, no container of its own), `data/` (`grafana/`, `mariadb/`), `deployments/` (each app's Dockerfile and compose file, plus a combined top-level compose file — see **Combined Compose File**). Scoping tracked on a Wayfinder map ([#490](https://github.com/mholubinka1/home-monitoring/issues/490)). The GitHub repo rename landed via #496; the Docker Hub image rename ([ADR-0024](adr/0024-docker-hub-image-name-octopus-app.md), to `mholubinka1/octopus-app`) and the database's own `octopus`→`home_monitoring` rename both executed against the live Pi on 2026-10-01 ([#547](https://github.com/mholubinka1/home-monitoring/issues/547)).
_Avoid_: octopus-monitoring (only the pre-rename name)

**Data-Gathering Container**:
An independently deployable service, packaged under `apps/`, that polls one external data source and persists it to the shared `home_monitoring` database (see that term's entry). `octopus-app` and `hive-app` are the two so far.
_Avoid_: app, service (ambiguous once more than one container exists)

**`common`**:
The shared library package (`libs/common/`) both `octopus-app` and `hive-app` depend on: logging setup, MariaDB engine/session plumbing, the Schema Sync mechanism, and the cross-app `job_run` table. Not itself deployable — no Dockerfile, no entrypoint. Everything app-specific (domain models, retrieval/retry logic, config schema, CRUD beyond `job_run`) stays in that app rather than here. See [ADR-0020](adr/0020-shared-common-library.md).
_Avoid_: utils, shared (ambiguous outside this glossary entry)

**Combined Compose File**:
`deployments/docker-compose.yml`, a reference copy of the three services (`octopus-app`, `hive-app`, `home-monitoring-db`), kept in sync by hand with the Pi's live stack. That stack is a separate, combined compose file (`/home/pi/git/pi-desktop/docker/docker-compose.yml`, project name `docker`) shared with unrelated services, so this file is not itself deployed, and hand-syncing is the accepted arrangement (see #585). Has no service definitions of its own — it `include:`s the three per-app compose files (`deployments/octopus-app/docker-compose.yml`, `deployments/hive-app/docker-compose.yml`, `deployments/mariadb/docker-compose.yml`), which hold the service definitions within the repo (the combined file defines none of its own). The Pi's live `pi-desktop` compose, not these files, is authoritative for what actually runs. See [ADR-0021](adr/0021-uv-workspace-packaging.md) for the equivalent per-package pattern on the Python packaging side.
_Avoid_: the compose file (ambiguous once four compose files exist)

**Container Naming Pattern**:
A container's compose service name, its `container_name` and its directory under `/mnt/media/pi-media/containers/` are the same string: `octopus-app`, `hive-app` and `home-monitoring-db` (renamed from `energy-monitor`, `energy-monitor-db` and the service `mariadb`). Each directory holds `config/` and `log/` (the database also `data/`), mounted at `/config` and `/log` (the database: its own `config/` files, `/var/lib/mysql` and `/var/log/mysql`). Apps and Grafana reach the database by host IP and published port, never by container name. Pinned by `scripts/tests/test_deployment_layout.py`; the Pi's containers carry these names; the rename procedure is `deployments/CUTOVER_RUNBOOK.md`. See [ADR-0015](adr/0015-pi-media-per-container-bind-mounts.md).
_Avoid_: energy-monitor, energy-monitor-db, the `mariadb` service (retired names)

**octopus-app**:
The Octopus Energy data-gathering container, at `apps/octopus-app/octopus_app/` (relocated from this repo's former `app/` + `tests/` by the apps/libs/data/deployments restructure) — same responsibilities as before, just repackaged as one of several containers rather than the repo's sole app.
_Avoid_: the app, main app (ambiguous once `hive-app` exists)

**hive-app**:
A data-gathering container for British Gas Hive heating data (current/target temperature, mode, state, boost, and the now/next/later schedule) and outdoor weather (current/historical observations and a forecast), alongside `octopus-app`, at `apps/hive-app/hive_app/`. Scoped to heating only — no hot water, smart plugs, lights, or sensors, since none exist on the household's account. Weather observation/forecast polling is not yet built (issues #508/#510); see the `hive-app initial data scope` ticket on the Home Monitoring Wayfinder map (issue #494) for the full scope.
_Avoid_: hive (ambiguous with Apache Hive)

**Heating Status**:
hive-app's poll of the Hive thermostat via the community `apyhiveapi` library (no official Hive API exists — see `.agent-docs/research/hive-api-access-approach.md`): current/target temperature, mode, state, and boost, polled every 120 seconds (the community-standard cadence both the library and Home Assistant's Hive integration default to). The now/next/later schedule is stored as a JSON column rather than flat columns, a deliberate deviation from this schema's usual style — see [ADR-0017](adr/0017-json-column-for-heating-schedule.md).
_Avoid_: thermostat status, Hive state

**Hive Auth Notification**:
One of exactly two ntfy.sh messages hive-app sends about Hive authentication: **re-authentication required** (high priority, links to `deployments/hive-app/REAUTH_RUNBOOK.md` on `main`) when Cognito no longer recognises the remembered device and a live SMS 2FA code is needed (never because Hive's API merely timed out), and **authentication recovered** (default priority, no link) once auth works again after a delivered "required" alert. Sent to the topic `home-monitoring-hive-auth-ntfy-<guid-no-dashes>`, whose GUID is the only secret on a public ntfy.sh topic and so lives only in the Pi's `config.yml`. One "required" per incident, shared between startup auth and the poll. Recovery is operator-run: `python -m hive_app.login` via `docker exec -it hive-app`, which prompts for the SMS code and writes `hive_auth_state.json`. See [ADR-0018](adr/0018-ntfy-for-hive-reauth-alerting.md). Format: title `<app>: <short event>`, plain-sentence body, a status emoji tag plus optionally one context tag, `Click` only when a link helps.
_Avoid_: alert (too general — nothing else in this repo pushes notifications), re-auth alert (omits the recovered message)

**Weather Observation / Weather Forecast**:
hive-app's outdoor-temperature data, split into two concerns: **Weather Observation** is current/historical readings (temperature, humidity, pressure, wind, precipitation) polled hourly, sourced from the household's nearest Weather Underground personal weather station (`IBECKE4`, Beckenham) with Open-Meteo as fallback on failure — the same primary/fallback shape octopus-app's Agile Forecast Refresh already uses for Agile pricing. **Weather Forecast** is upcoming days' predicted max temperature, fetched hourly from Open-Meteo, feeding the Gas Cost Forecast's projection for remaining billing-period days. Both accumulate history only from hive-app's first successful poll onward — no backfill of pre-existing weather data.
_Avoid_: weather data (ambiguous between the two)

**Gas Cost Forecast**:
The extension of octopus-app's `cost_forecast` (previously implicitly electricity-only — `cost_forecast.py` hard-coded `_current_electricity_agreement`) to also cover gas, distinguished by a new `energy` column on the existing table rather than a parallel table — see [ADR-0016](adr/0016-energy-column-on-cost-forecast.md). Its actual-cost-to-date figure reuses the existing Agreement/product_rate join. Its projected-total figure currently uses the same average-recent-consumption projection method as electricity's non-Agile branch (issue #507) — a planned upgrade (issue #511) will replace this with a live linear regression of daily gas kWh (from `daily_consumption_summary`) against daily max outdoor temperature, computed in Python on every forecast run (nothing persisted, consistent with [ADR-0010](adr/0010-local-day-bucketing-python-vs-sql.md)'s preference for Python over stored derived state) and applied to Weather Forecast's upcoming max-temp figures for the billing period's remaining days.
_Avoid_: heating cost model, gas forecast (ambiguous with Weather Forecast)

**Presence-Based Heating Control**:
A deferred, explicitly last-phase capability: automatically preventing the heating from running when nobody is home, most likely via Hive's own geofencing/geolocation feature rather than a new integration (phone tracking, Home Assistant, etc.) — contingent on confirming `apyhiveapi` actually exposes that state, which is not yet known. Unlike every other Home Monitoring capability so far, this is control/actuation (writing to Hive), not passive data-gathering. Not yet started; tracked as a fog/research item on the Home Monitoring Wayfinder map.
_Avoid_: smart heating, occupancy detection (until the actual signal is confirmed)

# Home Monitoring

Data-gathering containers that poll household energy and heating sources and persist them to a shared MariaDB database for downstream Grafana dashboards.

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
A tariff contract period held against a meter, carrying a tariff code, product code, validity dates and price history.
_Avoid_: contract, plan

**Tariff Code / Product Code**:
Octopus's identifiers for a pricing plan; the product code is derived from the tariff code by regex.
_Avoid_: plan ID, rate code

**Tariff Type**:
The classification of a pricing plan — `variable`, `economy7`, `agile`, `fixed`, `prepay` or `go`. Only `economy7`, `agile` and `go` (a tariff code containing `-GO-`, e.g. Octopus Go `GO-VAR-22-10-14`) are currently detected in code.
_Avoid_: plan type, rate type

**Agile**:
Octopus's half-hourly dynamic electricity pricing tariff, detected via tariff code containing `AGILE`. Its rates are stored through the same generic path as every other product (see `PricingRetriever`), and a dedicated Agile cost forecast is still unbuilt (see Cost Forecast).
_Avoid_: dynamic tariff, agile octopus

**Octopus Go**:
Octopus's time-of-use electricity tariff: a cheap unit rate in a fixed overnight window (00:30–05:30 local time), published as two multi-hour `product_rate` windows per day rather than Agile's half-hourly slots.
_Avoid_: time-of-use tariff (too generic), off-peak tariff

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
An Octopus Energy account — holds an account number, address and postcode, and can have multiple meters.
_Avoid_: customer, user

### Data Storage

**`home_monitoring` database** (formerly `octopus`):
The single shared MariaDB database for `octopus-app` and `hive-app`, holding both apps' tables plus the cross-app `job_run` and `account_postcode` tables owned by `common`. It was renamed from `octopus` on 2026-10-01 ([ADR-0022](adr/0022-single-shared-home-monitoring-database.md)); models keep the literal `schema="octopus"`, which [ADR-0025](adr/0025-schema-translate-map-for-config-driven-mariadb-schema.md) translates to the configured database name at query time.
_Avoid_: octopus database (the old name), the database, mysql db

**schema** (as in `schema_translate_map`, `__table_args__ = {"schema": ...}`):
In this codebase `schema` is a synonym for database, since MariaDB has no schema/database distinction. It is unrelated to Schema Sync.
_Avoid_: schema (unqualified, when the reader might mean Schema Sync instead) — prefer "the `schema=` value" or "the database"

**Schema Sync**:
The additive-only reconciliation each app's MariaDB client runs on startup: it creates missing tables, columns and indexes, diffed against that app's own models, and never drops or alters existing ones. The mechanism lives in `common` ([ADR-0005](adr/0005-additive-only-schema-sync.md), [ADR-0022](adr/0022-single-shared-home-monitoring-database.md)).
_Avoid_: migration, schema migration (this project deliberately has no versioned migration tool)

**InfluxDB (legacy)**:
A former time-series store, described historically in the README; its implementation (`app/_deprecated/`) has been removed entirely — MariaDB is, and has been, the only active sink.
_Avoid_: the time-series DB (when referring to the current system)

**Local Day**:
The calendar day in the configured timezone (default `Europe/London`), used for every day-bucketed cost, consumption and weather figure. Raw `consumption.period_from`/`period_to` are UTC, so bucketing converts first (`common.local_day` in app code, `CONVERT_TZ` in Grafana queries), and the apps and dashboard must share one zone ([ADR-0010](adr/0010-local-day-bucketing-python-vs-sql.md)).
_Avoid_: UTC day, calendar day (when the raw UTC date is meant instead)

**Day Completeness**:
A strictly past Local Day must have its expected half-hourly `consumption` rows (48, or 46/50 on clock-change days) before its total is final, since settlement can lag more than 24 hours ([ADR-0009](adr/0009-day-completeness-guard-standing-charge-fallback.md)).
_Avoid_: data lag, settlement delay (when referring to the guard itself, not the underlying cause)

**Consumption Summary**:
The `daily_consumption_summary` table: a pruning-exempt daily aggregate of raw `consumption` per energy and Local Day, with `half_hour_count` recording how many readings sit behind each total. Populated by the daily `update_consumption_summary` job (re-summarising the trailing 14 days) and by the `yearly_comparison_backfill` at startup, which fetches what the API still serves without writing raw `consumption` ([ADR-0027](adr/0027-consumption-requests-use-descending-order.md)).
_Avoid_: daily total, consumption rollup

**Yearly Comparison**:
The pair of Grafana panels — monthly total over the trailing 12 months, and a week-over-week year-on-year change by ISO week — both reading `daily_consumption_summary`. The weekly panel only compares ISO weeks with all 7 days present, so partial current or boundary weeks don't skew the change.
_Avoid_: annual comparison, YoY chart

**Retention Window**:
The 45-day period after which `DataPruner` deletes raw `consumption` and `product_rate` rows, run daily only after the consumption summary has succeeded in the same cycle. `agreement` rows and derived tables such as `cost_forecast` and `daily_consumption_summary` are never pruned ([ADR-0003](adr/0003-90-day-data-retention.md)).
_Avoid_: data expiry, TTL

### Scheduling and Retrieval

**Startup Backfill**:
The historical consumption retrieval run on every process start, bounded by `retention_days`; its last-retrieved watermark is in memory only, so it re-runs in full on each restart.
_Avoid_: initial sync, bootstrap, one-time sync

**Consumption Backfill Job**:
A daily job (`consumption_backfill`, `DAILY_JOB_TIME`) that re-runs the full-window consumption retrieval, so gaps that fell behind the refresh cursor self-heal; the consumption summary, heating model refresh and pruning follow in the same run ([ADR-0011](adr/0011-periodic-consumption-backfill-full-window-reuse.md)).
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
The top-level facade wiring the Octopus API client and MariaDB client together; holds account and meter state for a run.
_Avoid_: app client, main client

**Job Run**:
A logged execution record (job name, status, timestamp) for each scheduled job, used to drive the dashboard's health and staleness panel.
_Avoid_: job log, task run

### Cost and Forecasting

**Billing Period**:
The tariff charge cycle an account is billed against, fetched from Octopus's GraphQL Kraken API (`account.billingOptions`) after exchanging the REST API key for a JWT via `obtainKrakenToken`. Kraken's period fields describe the statement window, so `BillingPeriod.from_billing_options` shifts them back a day; for flexible-billing accounts the end is one calendar month after the start, minus a further day (see `.agent-docs/research/octopus-billing-period-api.md`).
_Avoid_: billing cycle, invoice period

**Product / Product Rate**:
`Product` is Octopus's public catalogue entry for a tariff plan, distinct from `Agreement` (the account's actual contract). `Product Rate` is a product's unit rate and standing charge for a region and time period, stored uniformly for every product so actual cost and the price-curve panel read from the same table.
_Avoid_: tariff (when referring to the public catalogue rather than the account's own agreement)

**Actual Cost**:
Cost computed from real consumption × the real rates charged (`consumption` ⋈ `agreement` ⋈ `product_rate`), covering yesterday's cost and this Billing Period's cost so far.
_Avoid_: spend, actual spend

**Cost Forecast**:
A projection of total cost for the current Billing Period: actual cost to date plus the remaining days, using the Heating Model's expected gas for gas and, for Agile, the latest persisted Agile forecast tiled beyond its horizon. For Octopus Go each remaining half-hour is priced individually at its covering agreement's rate; past gap days with missing rates are still written, flagged with Estimated Rates.
_Avoid_: price forecast (that term refers to the underlying Agile price data, not the derived cost projection)

**Estimated Rates**:
The rates used to price a past gap day whose published rates are missing or incomplete: the time-weighted average of that day's own published segments, or else the nearest earlier fully published day's rates under the same agreement. At most 3 days per energy per refresh; beyond that the refresh fails ([ADR-0026](adr/0026-missing-published-rates-on-a-past-gap-day-are-estimated-and-flagged.md)).
_Avoid_: fallback rates, guessed rates

**Agile Predict**:
A third-party public service (`agilepredict.com`, backed by a Fly.io app) providing a hard-capped 14-day-ahead Agile price forecast per GSP region via `GET https://agilepredict.com/api/{region}/`, with no authentication. It is the primary source for the Agile Forecast Refresh job, consumed as an external API rather than reimplemented ([ADR-0002](adr/0002-agile-predict-forecast-dependency.md)).
_Avoid_: the forecast API, prediction service

**x2r.uk**:
A second third-party hobby forecast service (`api.x2r.uk`, independently hosted), providing a ~14-day-ahead Agile price forecast per GSP region, used as the Agile Forecast Refresh job's fallback when Agile Predict fails. Its naive timestamps are read as Europe/London, and it has its own client and mapping into `AgileForecastReading` ([ADR-0002](adr/0002-agile-predict-forecast-dependency.md)).
_Avoid_: the fallback API, backup forecast

**Agile Forecast Refresh**:
The hourly job that fetches Agile price forecast readings (Agile Predict first, x2r.uk on failure) and upserts them into `agile_forecast`. It runs independently of the Cost Forecast, so a forecast-source outage does not block the cost projection.
_Avoid_: forecast sync, price forecast job

**Cheap Window**:
The cheapest contiguous block of a given duration (30min/1h/2h/3h/4h/6h) within the next few days of half-hourly rates, computed live at query time rather than stored.
_Avoid_: best time to use power, price dip

**Tariff Projection**:
Half-hourly unit rates for the current non-Agile electricity tariff beyond its last published rate, each taken from the same local clock time on the latest fully published day.
_Avoid_: tariff forecast, estimated rates (that is a past-day cost term)

### Gas Heating Model

**Degree-day**:
A measure of how much heating a day needed: how far its temperature fell below a base temperature, summed over a period. The Heating Model uses a learned Heating Threshold on the Effective Temperature instead of a fixed base.
_Avoid_: HDD (unexplained)

**Effective Temperature**:
The temperature the Heating Model uses for a day, `(1 - w) * today + w * yesterday`, with `w` fitted from the data to capture thermal memory. It needs a complete day of weather ([ADR-0029](adr/0029-weather-history-backfilled-hourly-deduplicated-and-labelled-by-source.md)).

**Heating Model**:
The learned relationship between weather and daily gas, `gas = baseload + slope * max(0, threshold - effective temperature)`, fitted over a rolling 12 months and refitted weekly by octopus-app, which also projects 7 days ahead. The cost forecast and gas dashboard read its tables ([ADR-0030](adr/0030-learned-heating-model-and-its-adoption-by-the-cost-forecast.md)).
_Avoid_: regression (the older fixed-threshold method)

**Baseload**:
The gas used on a day with no heating (hot water and cooking), measured as a trailing 120-day moving average from warm days, interpolated across winter gaps and held flat at the ends. Away days (gas below about a third of baseload) are excluded from the fit.

**Heating Threshold**:
The effective temperature below which the Heating Model says heating is needed, learned from the data (about 12.6 C on the prototype, not the 15.5 C first assumed).

**Complete Gas Day**:
A Local Day with a gas total above zero and every expected half-hour reading (46, 48 or 50 on daylight-saving days), judged from the reading count stored with `daily_consumption_summary`. Partial and missing gas days are excluded, never filled in, because the Octopus API reports zero or no data for about 13% of days since 2024-10-09.
_Avoid_: zero day (a stored zero means missing data, not zero use)

**Gas Cost Forecast**:
The extension of `cost_forecast` to gas, distinguished by an `energy` column ([ADR-0016](adr/0016-energy-column-on-cost-forecast.md)). The projected total adds each remaining day's standing charge to its expected gas from the latest usable Heating Model's forecast days; days without a usable model fall back to average daily consumption ([ADR-0023](adr/0023-gap-filled-days-get-an-estimated-variable-cost.md)).
_Avoid_: heating cost model, gas forecast (ambiguous with Weather Forecast)

**Gas Heating Dashboard**:
The second Grafana dashboard (`data/grafana/gas-heating-dashboard.json`, queries in `data/grafana/mariadb/gas-heating-queries.md`), separate from the electricity dashboard and in London time. After the 2026-11-02 review it has six chart panels: temperatures, daily gas with expected-gas bars, heating cost per hour, minutes below comfort, the outdoor temperature band and the billing-period predictor.
_Avoid_: heating dashboard (ambiguous with the Hive app)

**Heating Hour**:
One UTC hour of heating, stored as a `heating_hour` row: minutes on, heating kWh and cost at the gas unit rate (no standing charge), and whether the kWh is measured or estimated ([ADR-0033](adr/0033-hourly-heating-cost-learns-a-hot-water-baseline-and-the-boilers-effective-kw.md)).
_Avoid_: boost cost (boost is only one way heating comes on)

**Hot-Water Baseline**:
The typical gas used in an hour of day with no heating on, learned from the last 28 days and subtracted from measured gas in heating hours.

**Effective Boiler kW**:
The gas the boiler draws per hour of heating-on time, learned from measured heating hours and used to estimate hours without measured gas.

### Thermal Model and Comfort

**Comfort Temperature**:
The room temperature below which the household usually reaches for boost: the weighted median room temperature at Boost Starts over the two years before a Local Day, each evidence point halving for every 60 Heating Days since it. It is learned, not the thermostat target ([ADR-0034](adr/0034-comfort-temperature-is-learned-from-the-room-temperature-at-boost-starts.md)).
_Avoid_: comfort target, target temperature (the thermostat's setting)

**Boost Start**:
The first thermostat reading of a boost, more than 10 minutes after any earlier boost reading. Its room temperature is the evidence the Comfort Temperature is learned from.

**Heating Day**:
A Local Day on which the heating was on at least once, by schedule or boost. Boost evidence ages by heating days, not calendar days.

**Counted Hour**:
A London clock hour in which the heating was on at least once in the 365 days before a Local Day. Only minutes in counted hours can count as below comfort.

**Comfort Day**:
One Local Day's stored comfort figures: its Comfort Temperature, its Counted Hours and the minutes the room was below comfort in them. A finished day is settled once and never restated.

**Thermal Model**:
The five learned numbers describing how the room warms and cools hour by hour: loss per degree above outdoors, gain per hour of heating, the gain carried into the next hour, sunshine gain and a constant gain ([ADR-0035](adr/0035-room-forecast-and-comfort-plan-come-from-a-small-learned-thermal-model.md)).
_Avoid_: heating model (that is the daily gas model)

**Backtest**:
A replay of each of the last 14 days by a Thermal Model fitted only on data before that day; its average and worst error decide whether the model is usable.

**Room Forecast**:
The predicted room temperature for each hour to the end of tomorrow, stored twice: under the Hive schedule as programmed and under the Comfort Plan.
_Avoid_: indoor forecast, temperature forecast (ambiguous with Weather Forecast)

**Day-Ahead Forecast**:
The Room Forecast for a day as it stood when that day began, never changed afterwards, so past days can be compared with what happened.

**Comfort Plan**:
The least heating that keeps the room at the Comfort Temperature (rounded up to a half degree) throughout the Comfort Window, including the pre-heat. Advice only; nothing is written to Hive.
_Avoid_: recommended schedule, optimised schedule

**Comfort Window**:
The configured hours the Comfort Plan keeps comfortable: 06:30 to midnight on weekdays and 07:45 to midnight at weekends by default. Distinct from Counted Hours, which are learned.

### Hive and Weather

**Heating Status**:
hive-app's poll of the Hive thermostat via the community `apyhiveapi` library (no official Hive API exists), storing current and target temperature, mode, state, boost and the now/next/later schedule every 120 seconds. `state` is thermostat demand (current below target), not boiler firing; the nullable `working` flag is the thermostat's own report and is unverified.
_Avoid_: thermostat status, Hive state

**Weekly Schedule**:
Hive's full seven-day heating schedule as programmed, stored by hive-app one row each time it changes. The schedule stored with each Heating Status poll is only the now, next and later slots.

**Waking-Hours Indoor Average**:
The Daily Outdoor Temperature Band's indoor line: the mean `current_temp` of Heating Status polls between the dashboard's Wake Up and Bedtime hours (defaults 7 and 23, London time), plotted only when at least 80% of the expected polls exist.
_Avoid_: average indoor temperature (ambiguous with the all-day mean)

**Hive Data Lost Notification**:
The ntfy.sh message hive-app sends when the newest `heating_status` row is more than 30 minutes old, checked every minute. It repeats hourly while data is missing, followed by a recovered notice; nothing is sent during quiet hours. Recovery from a genuine re-authentication failure is operator-run via `python -m hive_app.login` ([ADR-0031](adr/0031-hive-data-lost-alert-replaces-the-reauth-alert.md)).
_Avoid_: re-auth alert (removed), alert (too general)

**Weather Observation**:
Recent and historical hourly outdoor readings from Open-Meteo at the Weather Location, stored per source, location key and hour so a re-written hour updates its row. Earlier history from 2024-07-24 is backfilled from Open-Meteo's archive and labelled `open-meteo-archive` ([ADR-0029](adr/0029-weather-history-backfilled-hourly-deduplicated-and-labelled-by-source.md)).
_Avoid_: weather data (ambiguous between the two)

**Weather Forecast**:
Upcoming days' predicted max temperature and (nullable) min and mean temperatures, fetched hourly from Open-Meteo and feeding the Heating Model's forecast days.

**Hourly Weather Forecast**:
hive-app's stored Open-Meteo forecast of temperature and sunshine for each of the coming hours, beside the daily Weather Forecast.

**Weather Location**:
The coordinates hive-app's weather jobs poll. Explicit `location` config always wins; otherwise it is derived from the Account Postcode (geocoded via postcodes.io), falling back to IP geolocation, and cached in `weather_location` ([ADR-0028](adr/0028-weather-location-derived-lazily-from-account-postcode.md)). Every reading is stored under a location key (coordinates rounded to two decimals, never the postcode), so a changed location starts a new series.
_Avoid_: household location (implies a user-entered setting)

**Account Postcode**:
The Octopus account's property postcode, written by octopus-app to the one-row `account_postcode` table in `common` and read by hive-app to derive the Weather Location. octopus-app is its only writer.

**Presence-Based Heating Control**:
A deferred, last-phase capability to stop heating when nobody is home, most likely via Hive's own geofencing rather than a new integration. It is control (writing to Hive), not passive data-gathering, and is contingent on `apyhiveapi` exposing that state.
_Avoid_: smart heating, occupancy detection (until the actual signal is confirmed)

### Repository and Containers

**Home Monitoring**:
This repo, renamed from `octopus-monitoring` now that it hosts more than one data-gathering container. It holds `apps/` (deployable containers), `libs/` (`common`), `data/` (Grafana, MariaDB) and `deployments/`.
_Avoid_: octopus-monitoring (only the pre-rename name)

**Data-Gathering Container**:
An independently deployable service under `apps/` that polls one external data source and persists it to the shared `home_monitoring` database.
_Avoid_: app, service (ambiguous once more than one container exists)

**`common`**:
The shared library package (`libs/common/`) both apps depend on, imported as `common` but installed as `monitoring-common`, holding logging setup, MariaDB plumbing, Schema Sync and the cross-app `job_run` and `account_postcode` tables. It is not deployable, and app-specific logic stays in each app ([ADR-0020](adr/0020-shared-common-library.md)).
_Avoid_: utils, shared (ambiguous outside this glossary entry)

**Combined Compose File**:
`deployments/docker-compose.yml`, which `include:`s the three per-app compose files and is kept in sync by hand with the Pi's live stack, which is what actually runs (see #585).
_Avoid_: the compose file (ambiguous once four compose files exist)

**Container Naming Pattern**:
A container's compose service name, `container_name` and host directory under `/mnt/media/pi-media/containers/` are the same string (`octopus-app`, `hive-app`, `home-monitoring-db`), pinned by `scripts/tests/test_deployment_layout.py` ([ADR-0015](adr/0015-pi-media-per-container-bind-mounts.md)).
_Avoid_: energy-monitor, energy-monitor-db, the `mariadb` service (retired names)

**octopus-app**:
The Octopus Energy data-gathering container, at `apps/octopus-app/octopus_app/`.
_Avoid_: the app, main app (ambiguous once `hive-app` exists)

**hive-app**:
The data-gathering container for British Gas Hive heating data and outdoor weather, at `apps/hive-app/hive_app/`. Scoped to heating only, since no hot water, plugs, lights or sensors exist on the account.
_Avoid_: hive (ambiguous with Apache Hive)

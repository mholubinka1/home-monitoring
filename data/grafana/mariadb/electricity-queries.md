# Grafana Queries

One SQL block per panel, documenting the live dashboard. Reconciled directly against [`data/grafana/electricity-dashboard.json`](../electricity-dashboard.json) — a full export of the dashboard's JSON model — rather than against the specs, so this file now describes what's actually deployed, not just what was planned. Re-sync both files together whenever the dashboard changes; this doc and the JSON export are meant to never drift apart again.

**Status**: `agile_forecast` and `cost_forecast` are confirmed live and in use (no longer spec-only) — `cost_forecast` backs both the Billing Period Progress panel and the `billing_period_start`/`billing_period_end` dashboard variables; `agile_forecast` backs Agile Prices and, on an Agile account, the Cheapest Time Window table. The tariff-comparison feature these queries originally assumed was dropped entirely and never came back. The Gas row and Health row documented in earlier revisions of this file are **no longer part of the dashboard** — see the callout at the bottom of this file before assuming that was intentional.

Five Grafana dashboard variables are defined (`templating.list` in the JSON):

- `${region}` — the account's GSP region code (see **Region Code / GSP** in `.agent-docs/context.md`). Query: `SELECT DISTINCT region FROM product_rate;`
- `${billing_period_start}` / `${billing_period_end}` — pulled live from the most recent `cost_forecast` row for `energy = 'E'` (`ORDER BY computed_at DESC LIMIT 1`), formatted `YYYY-MM-DD`. The `energy` filter was added alongside the octopus-app gas cost forecast change (issue #507) — before that, `cost_forecast` held only electricity rows, so an unfiltered `ORDER BY computed_at DESC LIMIT 1` was implicitly electricity-only; once a gas row can be written in the same refresh, the same query without the filter would nondeterministically pick either energy's billing period. Not consumed by any panel query — they're interpolated directly into the **Billing Period Progress** panel's title so the billing window is visible without a separate table panel. All three variables now refresh On Time Range Change (`refresh: 2`) — `${region}` previously refreshed On Dashboard Load only, but was switched to match the other two at some point after `version: 19`. Since the dashboard's time range is relative (`now-3h` to `now+120h`), every 30-minute auto-refresh tick counts as a time range change, so all three variables now resync on the same cadence as the panels' own value queries instead of only on page load.
- `${rates_estimated_note}` — hidden (`hide: 2`). Empty string unless the latest `cost_forecast` row for `energy = 'E'` has `rates_estimated` set, in which case it reads `⚠ estimated rates: <comma-separated dates>` (issue #594, ADR-0026). Interpolated into the **Billing Period Progress** panel's title, so nothing extra shows when the forecast is not estimated. Refreshes On Time Range Change (`refresh: 2`) like the others.
- `${timezone}` — hidden constant (`hide: 2`, `type: constant`), default `Europe/London`. The IANA zone that defines a local day; every `CONVERT_TZ` in this file and in the dashboard takes it as its zone argument (see the **Local-time convention** paragraph below). It must be set to the same value as the apps' `timezone` setting. It is a constant, so it is edited in Dashboard settings, Variables, or in the `query` and `current` fields of the JSON; edits made only in the Grafana UI are lost when the dashboard is re-imported from the JSON.

**Query target database**: every panel and annotation target carries `"dataset": "home_monitoring"` (was `"octopus"` until the database rename, ADR-0022; the old `octopus` database has since been dropped, so the stale value made every panel return a 500, issue #584). The literal `octopus` in `schema="octopus"` in the app models is a different thing, a fixed placeholder (ADR-0025).

Two built-in annotation markers are also defined (`annotations.list` in the JSON, alongside the default "Annotations & Alerts" query): a purple `+24h` line and a dark-purple `0h` ("now") line, both filtered to render on panel ids 3 (Agile Prices) and 23 (Current Tariff Prices) only.

**Dashboard-level time range**: `from: now-3h`, `to: now+120h`, `refresh: 5m`. This is what actually gives the Agile Prices panel its forward lookahead — not a panel-level `timeFrom`/`timeShift` override (Grafana's per-panel relative-time override can only push the *start* earlier; it always hardcodes the end to literal "now", so it structurally cannot show forecast data past the current moment). Setting the dashboard's own default range to end at `now+120h` sidesteps that limitation entirely, since the ceiling only applies to panel-level overrides, not the dashboard's own top-level range. Any panel that needs a *different, fixed* window from this shared default carries its own `timeFrom`/`timeShift` (documented per panel below).

**Note on the lookahead figure**: this has moved twice since the dashboard's original 72-hour Agile Prices requirement — first to `+48h`, now to `+120h` (5 days), likely to match the Cheapest Time Window table's own 5-day forecast horizon. Each of these was a manual Grafana edit never reconciled back into this file until now — worth setting up a habit of re-exporting after any live dashboard change, rather than letting version drift accumulate (this sync jumped from `version: 19` to `version: 36`, 17 unreconciled edits). The Cheapest Time Window table's `5days` column still independently reaches 5 days out via `agile_forecast` regardless of this dashboard-level setting, since that panel is a Table with no time axis. (Its middle column was renamed `72hrs` → `3days` when the dashboard's own window was still 48h; see that panel below.)

**Datasource portability fixed**: this export uses Grafana's "export for sharing externally" format — every panel references the datasource as `${DS_MYSQL}` (an `__inputs`-declared placeholder), not a hardcoded UID. Earlier exports hardcoded the literal datasource UID (`aftel9qlylts0e`), which would have broken on any Grafana instance where that UID didn't already exist (e.g. after a full rebuild where the MySQL datasource gets re-added with a new random UID, since it's not provisioned via YAML). Importing this JSON now prompts for a datasource mapping instead of silently failing. Dashboard `uid` is `afu5ghhf1e29sb`, `version: 38`.

## Schema assumed

```text
consumption               (existing) id, energy, period_from, period_to, raw_value, unit, est_kwh
agreement                 (existing) id, energy, product_code, tariff_code, valid_from, valid_to
product                   (existing) product_code PK, display_name, direction
product_rate              (existing) id, product_code, region, valid_from, valid_to, unit_rate, standing_charge
job_run                   (existing) id, job_name, status, ran_at, error_message
daily_consumption_summary (existing) energy, date PK(energy, date), total_kwh
agile_forecast             (live) id, region, period_from, period_to, forecast_unit_rate, fetched_at
tariff_projection          (live) id, product_code, region, period_from, period_to, unit_rate, projected_at
cost_forecast               (live) id, billing_period_start, billing_period_end, actual_cost_to_date,
                                    projected_total_cost, computed_at, energy,
                                    rates_estimated, estimated_days
```

`agile_forecast` caches the raw half-hourly AgilePredict response (real 14-day forecast only) for charting. `tariff_projection` holds the **Tariff Projection**: octopus-app rewrites it on every pricing refresh with half-hourly rates for the current non-Agile electricity tariff, from the end of its published rates to six days ahead, each at the same local clock time on the latest fully published day (empty on Agile). `cost_forecast` is the billing-period-level summary the app computes hourly (actual cost so far + full-period projection, using tiled forecast data internally beyond day 14 — that tiling isn't persisted point-by-point, only the summary is).

**Join convention — half-open windows only.** Any query joining `consumption` to `product_rate` or `agreement` on a `valid_from`/`valid_to` window must use a half-open range: `c.period_from >= valid_from AND c.period_from < COALESCE(valid_to, '9999-12-31 23:59:59')`. Never `BETWEEN valid_from AND COALESCE(valid_to, '9999-12-31 23:59:59')` (inclusive on both ends) — `consumption.period_from` sits on the exact same half-hourly grid as these windows, and adjacent windows are back-to-back (one row's `valid_to` equals the next row's `valid_from`), so an inclusive-both-ends join matches a consumption row against *two* rate rows instead of one, silently doubling every `SUM(est_kwh * unit_rate)` in the query. Confirmed live: before the fix, the Yesterday's Cost panel showed £6.01 — roughly double the £3.19 the corrected query returns for the same day (the official Octopus app showed £3.25 for that day; that residual gap turned out to be a second, distinct bug — see the local-time convention below and issue #434 for the join-doubling investigation).

**`product_rate` join performance — use a correlated subquery, not a range predicate.** The half-open range predicate above is correct but, against `product_rate` specifically, is a performance trap: MariaDB cannot turn a two-sided open range (`valid_from <= X AND X < valid_to`) into an indexed seek against the composite index `(product_code, region, valid_from, valid_to)`, since it can't know in advance that the windows are non-overlapping. It falls back to a Block Nested Loop join — a full scan of both `consumption` and `product_rate` compared row-by-row. Confirmed live against production (4,199 `consumption` rows × 37,075 `product_rate` rows): a range-predicate join against this table took **88.9s** (`EXPLAIN` showed `type: ALL` on both tables, even with the index available as a `possible_key`). Rewritten as a correlated subquery — "find the single most-recent `product_rate` row with `valid_from <= X`, `ORDER BY valid_from DESC LIMIT 1`" — the same index supports an actual indexed descent (`EXPLAIN` shows `eq_ref`, 1 row), and the same query returned the same values (row-for-row verified) in **11.8s**. Every panel joining `consumption` to `product_rate` uses this form:

```sql
JOIN product_rate pr
  ON pr.id = (
    SELECT pr2.id FROM product_rate pr2
    WHERE pr2.product_code = a.product_code
      AND pr2.region = '${region}'
      AND pr2.valid_from <= c.period_from
    ORDER BY pr2.valid_from DESC
    LIMIT 1
  )
```

instead of the `valid_from`/`valid_to` range-predicate join. The subquery also requires the half-hour to fall before the chosen row's own `valid_to` (`AND c.period_from < COALESCE(pr2.valid_to, ...)`): with the row's validity unchecked, a half-hour whose rate has not been published (or was pruned) would silently be priced at an older rate. Now it finds no row, the join drops the half-hour, and the day's completeness guard excludes the day. The agreement join above it already bounds each half-hour by the agreement's `valid_from`/`valid_to`, so across a tariff change every half-hour is priced by the product of the agreement covering it. This doesn't apply to the `agreement` join (only 7 rows in production — a full scan there is cheap regardless), nor to Agile Prices (it queries `product_rate` directly by its own `valid_from`, no interval join against it). The Cheapest Time Window table uses the correlated-subquery form against its generated half-hour slots. Every panel joining `consumption` to `product_rate` now uses this form — `Yesterday's Cost (Electricity)` previously used the slow range-predicate form and has since been fixed (as had the since-removed Latest Consumption panel's cost query).

**Row 2 lookback windows are capped at the retention window (45 days), not 90 days or 12 weeks.** No pruning job actually deletes old `consumption` rows yet (see **Retention Window** in `.agent-docs/context.md`) — the real reason the table is short-lived is that `retention_days` (45) bounds the Startup Backfill's lookback, so the app never fetches more than 45 days of history from Octopus at once. Any query with a longer lookback than that silently returns less data than it appears to ask for, not an error. Panels below that read raw `consumption` are written with a 45-day window for this reason. Monthly Total Consumption and the Year-on-Year panel read `daily_consumption_summary` instead (exempt from this cap) since they only need daily kWh totals.

**Local-time convention — group and label by the local day (the `timezone` dashboard variable), not raw UTC.** `period_from` is stored as true UTC. Any query that groups or labels by calendar day (`DATE(...)`) or hour-of-day (`HOUR(...)`, `DAYNAME(...)`) must first convert to local time: `CONVERT_TZ(period_from, 'UTC', '${timezone}')`. `timezone` is a constant dashboard variable (Dashboard settings, Variables) defaulting to Europe/London; set it to the same zone as the apps' `timezone` setting (see [ADR-0010](../../../.agent-docs/adr/0010-local-day-bucketing-python-vs-sql.md)), and never write the zone name into a query. During BST in the default zone this shifts the effective day/hour boundary back by an hour from raw UTC. `CONVERT_TZ` requires MariaDB's named-timezone tables to be loaded (confirmed present on the production instance); queries that don't group or label by day/hour don't need it, since every other timestamp comparison in this file is a plain UTC-to-UTC instant comparison.

**Field-formatting convention.** Set Grafana's field unit per column, per category, rather than leaving raw numbers unformatted:

- Cost columns already converted to pounds (`cost_gbp`, `*_cost_gbp`) → Grafana's currency (GBP, £) unit (`currencyGBP`).
- Rate columns still in pence/kWh (`rate_pence_per_kwh`, `rate`, etc.) → a custom unit of `p/kwh` (lowercase — this is the exact string the live Agile Prices panel uses, corrected from an earlier `p/kw` typo; Grafana custom unit strings are compared verbatim, so use this exact casing rather than `p/kWh` if adding the unit to any other panel) — these are deliberately *not* divided by 100, unlike the cost columns above, so don't apply the GBP unit to them.
- Energy columns (`*_kwh`, `est_kwh`) → a custom unit of `kWh` (`kwatth`).
- Percentage-change columns → Grafana's percent unit.

---

## Current Tariff

### Current Tariff — stat, id 22

Full-width banner at the top of the dashboard naming the account's current electricity tariff by its product display name (for example "Octopus Go"), falling back to the tariff code when the product is not in the catalogue. Reads the agreement in force now. Shows "no current agreement" when none matches. The panel selects the `tariff` field explicitly (`reduceOptions.fields` is `/^tariff$/`), because an empty selector shows numeric fields only and would hide the name. Every other panel sits below it.

```sql
SELECT COALESCE(p.display_name, a.tariff_code) AS tariff
FROM agreement a
LEFT JOIN product p ON p.product_code = a.product_code
WHERE a.energy = 'E'
  AND a.valid_from <= NOW()
  AND NOW() < COALESCE(a.valid_to, '9999-12-31 23:59:59')
ORDER BY a.valid_from DESC LIMIT 1;
```

---

## Row 1 — Cost Summary

### Yesterday's Cost (Electricity) — timeseries, id 1

**Redesigned from a Stat panel to a genuine Timeseries.** Previously this was a Stat panel (`Graph mode: Area`, `Calcs: Last (not null)`) paired with a separate "As Of" companion panel just to show which date the reducer had landed on — necessary because a Stat panel's big number has no inherent date context. Now that it's a real Timeseries, the date is visible directly (axis/tooltip), so the **As Of panel (former id 11) has been removed entirely** rather than fixed further. `timeFrom: 45d` (panel-level override, independent of the dashboard's shared `-3h`/`+48h` range). `noValue: "0"`.

Thresholds changed to match the same blue/green/yellow/orange/red band style used elsewhere on this dashboard (Load Shift Efficiency, Cheapest Time Window): blue below £1, green from £1, yellow from £3, orange from £4, red from £5 (previously just green/amber/red at £3/£5). `min: -1` (was `0`); the field previously also carried a `max: 19` cap, since removed so the axis auto-scales. `thresholdsStyle: area` renders the bands as a coloured background rather than just axis colouring. A field override forces the `cost_gbp` series line itself to a fixed purple colour, independent of the threshold-driven background.

The day's standing charge is the one in force at local midday (the half-hour starting at wall-clock 12:00 local, which exists on every day including clock-change days; the app's elapsed-cost read uses the same rule; estimated gap days keep their existing twelve-elapsed-hours midday, which differs only on the two clock-change days), so a tariff change part-way through a day takes one agreement's charge rather than the larger of the two. It falls back to the larger only if that half-hour has no consumption row. The same rule applies to Daily Average Cost below.

The `product_rate` join fix from earlier (correlated subquery instead of the range-predicate form that measured at 88.9s elsewhere in this file) is unchanged and still in place.

```sql
SELECT
  DATE(CONVERT_TZ(c.period_from, 'UTC', '${timezone}')) AS time,
  ROUND((SUM(c.est_kwh * pr.unit_rate) + COALESCE(MAX(CASE WHEN TIME(CONVERT_TZ(c.period_from, 'UTC', '${timezone}')) = '12:00:00' THEN pr.standing_charge END), MAX(pr.standing_charge))) / 100, 2) AS cost_gbp
FROM consumption c
JOIN agreement a
  ON a.energy = c.energy
 AND c.period_from >= a.valid_from
 AND c.period_from < COALESCE(a.valid_to, '9999-12-31 23:59:59')
JOIN product_rate pr
  ON pr.id = (
    SELECT pr2.id FROM product_rate pr2
    WHERE pr2.product_code = a.product_code
      AND pr2.region = '${region}'
      AND pr2.valid_from <= c.period_from
      AND c.period_from < COALESCE(pr2.valid_to, '9999-12-31 23:59:59')
    ORDER BY pr2.valid_from DESC
    LIMIT 1
  )
WHERE c.energy = 'E'
  AND c.period_from >= CURDATE() - INTERVAL 45 DAY
  AND c.period_from < CURDATE()
GROUP BY DATE(CONVERT_TZ(c.period_from, 'UTC', '${timezone}'))
HAVING COUNT(*) = TIMESTAMPDIFF(MINUTE,
  CONVERT_TZ(CAST(time AS DATETIME), '${timezone}', 'UTC'),
  CONVERT_TZ(CAST(time + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
) / 30
ORDER BY time;
```

### Billing Period Progress ($billing_period_start → $billing_period_end) — bargauge, id 2

Title also ends with `${rates_estimated_note}`, which shows `⚠ estimated rates: <dates>` when the latest forecast priced a past day at estimated rates and nothing otherwise. Title interpolates the `${billing_period_start}`/`${billing_period_end}` dashboard variables directly — this is what replaced the separate "Current Billing Period" table panel from earlier drafts of this dashboard. `Display mode: Gradient` (was `LCD`), `Value mode: Color`, reduced with `Calcs: Max`. Field overrides rename `billing_period_cost_gbp` → "Current Spend" and `projected_cost_gbp` → "Projected Spend". Thresholds: green / yellow (£60) / semi-dark-orange (£80) / dark-red (£100). Has a `configFromData` transformation that derives the gauge's max from `billing_period_cost_gbp` via a `max` reducer, followed by `filterFieldsByName`.

`WHERE energy = 'E'` was added alongside issue #507's gas cost forecast — `cost_forecast` can now hold a gas row from the same refresh, computed at the same or a near-identical `computed_at`, so an unfiltered `ORDER BY computed_at DESC LIMIT 1` would nondeterministically surface either energy's figures on what was previously an implicitly-electricity-only panel.

**Deployment ordering matters here.** `cost_forecast.energy` is nullable (see ADR-0016) — Schema Sync adds the column but never backfills it, so every pre-migration row reads `energy IS NULL` until the one-time manual `UPDATE` runs. If this dashboard version is imported into Grafana *before* the app has been redeployed with the #507 code (or before that redeploy's first `cost_forecast_refresh` has written a fresh `energy = 'E'` row), this filtered query returns zero rows and the panel goes blank instead of showing the last known forecast. The same ordering applies to `rates_estimated`/`estimated_days` (issue #594): the `${rates_estimated_note}` variable selects them, so importing this dashboard before the app has been redeployed (Schema Sync adds the columns on startup) makes that variable's query fail on an unknown column. Deploy the app first — its own eager startup sync (`run_initial_cost_forecast_sync`) writes a fresh electricity row before the scheduler even starts — then import this dashboard version.

```sql
SELECT actual_cost_to_date AS billing_period_cost_gbp, projected_total_cost AS projected_cost_gbp
FROM cost_forecast
WHERE energy = 'E'
ORDER BY computed_at DESC
LIMIT 1;
```

---

## Row 2 — Electricity

### Current Tariff Prices — timeseries, id 23

Top of the right-hand column, above Agile Prices. Half-hourly unit rates of the tariff covering each half-hour across the dashboard's whole range (`$__timeFrom()` to `$__timeTo()`, so now-3h to now+120h by default), priced the same way as the Cheapest Time Window below: the `slot_products` CTE gives each half-hour the product of the agreement covering it, and each takes the published `product_rate` first, then the **Tariff Projection** (`tariff_projection`, ADR-0032) on a non-Agile tariff, then `agile_forecast` on an Agile one. It returns three wide columns, `published`, `projected` and `forecast`, one non-null per row, so the field overrides can match fixed names: `projected` and `forecast` are drawn dashed, `published` solid. The last published half-hour before a projected or forecast one also carries its rate in that dashed column (found with one `LEAD()` pass over the combined rates, not a per-row lookup): a step line draws nothing after its last point, so without it that half-hour would show as a gap. Lines step after each point (`stepAfter`), so a multi-hour Go window draws flat, and are coloured by the same price bands as Agile Prices (unit `p/kwh`). On Octopus Go this shows tonight's and the coming nights' cheap windows before Octopus publishes them. The dashboard-level `+24h` and `0h` markers also render on this panel, which marks the next 24 hours.

```sql
WITH RECURSIVE slots AS (
  SELECT TIMESTAMP(DATE($__timeFrom()), SEC_TO_TIME(FLOOR(TIME_TO_SEC(TIME($__timeFrom())) / 1800) * 1800)) AS time
  UNION ALL
  SELECT time + INTERVAL 30 MINUTE FROM slots
  WHERE time + INTERVAL 30 MINUTE < $__timeTo()
),
slot_products AS (
  SELECT s.time, COALESCE(
    (SELECT a.product_code FROM agreement a
     WHERE a.energy = 'E'
       AND a.valid_from <= s.time
       AND s.time < COALESCE(a.valid_to, '9999-12-31 23:59:59')
     ORDER BY a.valid_from DESC LIMIT 1),
    (SELECT a.product_code FROM agreement a
     WHERE a.energy = 'E'
       AND a.valid_from <= s.time
       AND a.valid_from < COALESCE(a.valid_to, '9999-12-31 23:59:59')
     ORDER BY a.valid_from DESC LIMIT 1)
  ) AS product_code
  FROM slots s
),
actual_rates AS (
  SELECT sp.time, pr.unit_rate AS rate
  FROM slot_products sp
  JOIN product_rate pr
    ON pr.id = (
      SELECT pr2.id FROM product_rate pr2
      WHERE pr2.product_code = sp.product_code
        AND pr2.region = '${region}'
        AND pr2.valid_from <= sp.time
        AND sp.time < COALESCE(pr2.valid_to, '9999-12-31 23:59:59')
      ORDER BY pr2.valid_from DESC
      LIMIT 1
    )
),
projected_rates AS (
  SELECT tp.period_from AS time, tp.unit_rate AS rate
  FROM tariff_projection tp
  JOIN slot_products sp ON sp.time = tp.period_from
  WHERE tp.product_code = sp.product_code
    AND tp.region = '${region}'
    AND NOT EXISTS (
      SELECT 1 FROM product_rate pr3
      WHERE pr3.product_code = sp.product_code
        AND pr3.region = '${region}'
        AND pr3.valid_from <= sp.time
        AND sp.time < COALESCE(pr3.valid_to, '9999-12-31 23:59:59')
    )
),
forecast_rates AS (
  SELECT f.period_from AS time, f.forecast_unit_rate AS rate
  FROM agile_forecast f
  JOIN slot_products sp ON sp.time = f.period_from
  WHERE sp.product_code REGEXP '^AGILE-(FLEX-)?[0-9]'
    AND f.region = '${region}'
    AND NOT EXISTS (SELECT 1 FROM actual_rates a WHERE a.time = f.period_from)
),
sourced_rates AS (
  SELECT time, rate, 'published' AS source FROM actual_rates
  UNION ALL
  SELECT time, rate, 'projected' FROM projected_rates
  UNION ALL
  SELECT time, rate, 'forecast' FROM forecast_rates
),
sequenced_rates AS (
  SELECT time, rate, source, LEAD(source) OVER (ORDER BY time) AS next_source
  FROM sourced_rates
)
SELECT time,
  CASE WHEN source = 'published' THEN rate END AS published,
  CASE WHEN 'projected' IN (source, next_source) THEN rate END AS projected,
  CASE WHEN 'forecast' IN (source, next_source) THEN rate END AS forecast
FROM sequenced_rates
ORDER BY time;
```

### Agile Prices: Today/Tomorrow (Actual + Forecast) — timeseries, id 3

No panel-level time override — relies entirely on the dashboard's shared `now-3h` to `now+120h` range for its lookahead (see the dashboard-level time range note at the top of this file). Threshold *lines* (not fill) at the exact price bands used elsewhere on this dashboard: light-blue below 0p, green from 0p, yellow from 10p, semi-dark-orange from 20p, red from 25p — same bands as the Cheapest Time Window table's colour coding below (and reused as-is by the Daily Average Unit Price panel further down), applied here as reference lines rather than cell colours. Unit is `p/kwh` (see the field-formatting convention note above). The panel is always about Agile, whatever tariff the account is on: the `agile_product` CTE takes the account's current product when it is an Agile one, otherwise the newest Agile product in the `product` catalogue (matched strictly as `AGILE-<year>…`, so the retired `AGILE-FLEX-…` code, which sorts after it, is not picked; its comparison rates are synced for every available product). Not being on Agile therefore leaves the panel unchanged. The forecast branch excludes any half-hour `product_rate` already has a row for, preferring the actual rate over `agile_forecast` wherever both exist — same precedence rule as the Cheapest Time Window table below, applied here so the panel never plots two conflicting points for one timestamp (e.g. during an `agile_forecast` refresh outage, where stale forecast rows would otherwise linger alongside newly-published actual rates for the same slots). The dashboard-level `+24h` and `0h` annotation markers (see the annotations note at the top of this file) are filtered to render only on this panel and Current Tariff Prices.

```sql
WITH agile_product AS (
  SELECT COALESCE(
    (SELECT a.product_code FROM agreement a
     WHERE a.energy = 'E'
       AND a.product_code REGEXP '^AGILE-(FLEX-)?[0-9]'
       AND a.valid_from <= NOW()
       AND NOW() < COALESCE(a.valid_to, '9999-12-31 23:59:59')
     ORDER BY a.valid_from DESC LIMIT 1),
    (SELECT p.product_code FROM product p
     WHERE p.product_code REGEXP '^AGILE-[0-9]'
     ORDER BY p.product_code DESC LIMIT 1)
  ) AS product_code
)
SELECT valid_from AS time, unit_rate AS rate_pence_per_kwh, 'actual' AS series
FROM product_rate
WHERE product_code = (SELECT product_code FROM agile_product)
AND region = '${region}'
AND valid_from >= CURDATE()

UNION ALL

SELECT period_from AS time, forecast_unit_rate AS rate_pence_per_kwh, 'forecast' AS series
FROM agile_forecast f
WHERE f.region = '${region}'
  AND f.period_from >= NOW()
  AND NOT EXISTS (
    SELECT 1 FROM product_rate pr2
    WHERE pr2.product_code = (SELECT product_code FROM agile_product)
    AND pr2.region = '${region}'
    AND pr2.valid_from = f.period_from
  )
ORDER BY time;
```

Query B draws the **Effective charging limit** from the hypervolt-agile scheduler as a flat dashed purple line across the whole time range, hidden from the legend and tooltip. It reads the most recent `fuel_price_threshold` row twice, once stamped at the start of the range and once at the end. Until hypervolt-agile has recorded a row the line is simply absent.

```sql
SELECT $__timeFrom() AS time, effective_limit_incl_vat AS effective_limit_p_per_kwh
FROM (SELECT effective_limit_incl_vat FROM fuel_price_threshold ORDER BY recorded_at DESC LIMIT 1) latest
UNION ALL
SELECT $__timeTo(), effective_limit_incl_vat
FROM (SELECT effective_limit_incl_vat FROM fuel_price_threshold ORDER BY recorded_at DESC LIMIT 1) latest
ORDER BY time;
```

### Load Shift Efficiency — timeseries, id 5

Replaces the two-line "p/kWh Efficiency vs Day's Avg Rate" panel from earlier drafts with a single derived percentage: how much cheaper your actual weighted-average rate is than the day's flat average rate, i.e. how well consumption is shifted toward cheap half-hours. Positive = shifted toward cheap hours; negative = shifted toward expensive ones. `timeFrom: 45d`. Thresholds: dark-red below 0%, green from 0%, blue from 20%. `min: -50`, `max: 51`.

```sql
SELECT
  DATE(CONVERT_TZ(c.period_from, 'UTC', '${timezone}')) AS time,
  ROUND(
    (AVG(pr.unit_rate) - (SUM(c.est_kwh * pr.unit_rate) / NULLIF(SUM(c.est_kwh), 0)))
      / NULLIF(AVG(pr.unit_rate), 0) * 100,
    2
  ) AS load_shift_efficiency_pct
FROM consumption c
JOIN agreement a
  ON a.energy = c.energy
 AND c.period_from >= a.valid_from
 AND c.period_from < COALESCE(a.valid_to, '9999-12-31 23:59:59')
JOIN product_rate pr
  ON pr.id = (
    SELECT pr2.id FROM product_rate pr2
    WHERE pr2.product_code = a.product_code
      AND pr2.region = '${region}'
      AND pr2.valid_from <= c.period_from
      AND c.period_from < COALESCE(pr2.valid_to, '9999-12-31 23:59:59')
    ORDER BY pr2.valid_from DESC
    LIMIT 1
  )
WHERE c.energy = 'E'
  AND c.period_from >= NOW() - INTERVAL 45 DAY
GROUP BY DATE(CONVERT_TZ(c.period_from, 'UTC', '${timezone}'))
HAVING COUNT(*) = TIMESTAMPDIFF(MINUTE,
  CONVERT_TZ(CAST(time AS DATETIME), '${timezone}', 'UTC'),
  CONVERT_TZ(CAST(time + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
) / 30
ORDER BY time;
```

### Average Consumption Per Day — barchart, id 6

Same query as earlier drafts' "Day-of-Week Average Consumption", renamed. `timeFrom: 45d`.

```sql
SELECT
  DAYNAME(d) AS day_of_week,
  ROUND(AVG(daily_kwh), 3) AS avg_kwh
FROM (
  SELECT DATE(CONVERT_TZ(period_from, 'UTC', '${timezone}')) AS d, SUM(est_kwh) AS daily_kwh
  FROM consumption
  WHERE energy = 'E'
    AND period_from >= NOW() - INTERVAL 45 DAY
  GROUP BY DATE(CONVERT_TZ(period_from, 'UTC', '${timezone}'))
  HAVING COUNT(*) = TIMESTAMPDIFF(MINUTE,
    CONVERT_TZ(CAST(d AS DATETIME), '${timezone}', 'UTC'),
    CONVERT_TZ(CAST(d + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
  ) / 30
) daily
GROUP BY DAYNAME(d)
ORDER BY FIELD(DAYNAME(d), 'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday');
```

### Cheapest Time Window — table, id 13

**No panel title** — cleared intentionally (see the "remove a panel title" note in project history); identifiable only by its field override renaming `window_size` → "Cheapest Time Window", and by the heading below. Represented in `dashboard.json` as an explicit `"title": ""` rather than an omitted key, so the blank title round-trips reliably through Grafana's export/import rather than depending on unspecified default behaviour for a missing field.

Follows the account's tariff, not Agile specifically: the `slots` CTE generates every half-hour for the next 5 days, `slot_products` gives each the product of the agreement covering it (by `valid_from`/`valid_to`; in a gap between agreements or past the last one, the latest agreement that had begun by then, since a contract is expected to renew; a future-dated tariff change is priced from its start) and each then takes whichever `product_rate` row of that product covers it, so a tariff publishing multi-hour windows (Octopus Go: a cheap 00:30–05:30 local-time window, one hour earlier in UTC during BST, and a peak rate for the rest of the day) is expanded to the same half-hourly series as Agile rates, keeping the `ROWS BETWEEN` window averages correct. `agile_forecast` fills a slot only where the product covering it is an Agile one; on any other tariff `tariff_projection` (the **Tariff Projection**, ADR-0032) fills every slot without a published rate, matched on the covering product, so on Octopus Go, which Octopus publishes only to the end of the current day, every column still sees the coming nights' cheap windows. Precedence is published `product_rate`, then `tariff_projection`, then `agile_forecast`. A projected slot is dropped when a `product_rate` row covers it, checked against the indexed table rather than the `actual_rates` CTE: MariaDB re-evaluates a CTE for every probe, and with the table's twelve scalar subqueries that took the query from about 120 MiB to about 380 MiB of server memory on the Pi, close to the database's 512 MB cap. 4 rows (`1h`/`2h`/`3h`/`4h` block sizes) × 3 columns (`24hrs`/`3days`/`5days` lookahead horizons — the middle column was renamed from `72hrs` to `3days`, same underlying 3-day cutoff, just relabeled once the dashboard's own forward window settled at 48h rather than 72h), wide-format like the Heatmap below. `product_rate` only reliably covers ~1–2 days ahead (Octopus publishes Agile rates the evening before), so on Agile the 3-day/5-day columns fall back to `agile_forecast` for any half-hour beyond what's actually been published, preferring actual rates over forecast wherever both exist. A window only counts for a horizon if it fits entirely inside it. Colour-coding is baked into the cell text itself as an emoji (🔵/🟢/🟡/🟠/🔴) rather than via Grafana thresholds, since Table panels can't apply numeric threshold-based cell colouring to a field whose displayed value is a composed string (time + rate) — SQL-side colouring sidesteps that limitation entirely and is portable across Grafana versions. Bands: `<0` blue, `[0,10)` green, `[10,20)` yellow, `[20,25]` orange, `>25` red. Field override renames `window_size` → "Cheapest Time Window". A window is only offered when every one of its half-hours has a rate (the window functions check both the count and that the span is unbroken), so a series that stops at the last published rate never reports a partial block as a full one. The final select makes one pass over `windows`: it unpivots the four block sizes (`blocks`), ranks each size within each horizon with `ROW_NUMBER()` (`ranked_blocks`) and pivots the cheapest back into the 4 × 3 table, starting from `block_sizes` so a row is returned even when it has no full block. It used to be twelve scalar subqueries, each re-evaluating `windows`; once the Tariff Projection filled five days of Go rates, that took about +380 MiB of server memory on the Pi against the database's 512 MB cap, and a heavier variant OOM-killed it. Check peak server memory on a live-configured copy after changing this query. Ties (identical average rate) are broken arbitrarily by whichever row `ROW_NUMBER()` puts first.

```sql
WITH RECURSIVE slots AS (
  SELECT TIMESTAMP(DATE(NOW()), SEC_TO_TIME(CEIL(TIME_TO_SEC(TIME(NOW())) / 1800) * 1800)) AS time
  UNION ALL
  SELECT time + INTERVAL 30 MINUTE FROM slots
  WHERE time + INTERVAL 30 MINUTE < NOW() + INTERVAL 5 DAY
),
slot_products AS (
  SELECT s.time, COALESCE(
    (SELECT a.product_code FROM agreement a
     WHERE a.energy = 'E'
       AND a.valid_from <= s.time
       AND s.time < COALESCE(a.valid_to, '9999-12-31 23:59:59')
     ORDER BY a.valid_from DESC LIMIT 1),
    (SELECT a.product_code FROM agreement a
     WHERE a.energy = 'E'
       AND a.valid_from <= s.time
       AND a.valid_from < COALESCE(a.valid_to, '9999-12-31 23:59:59')
     ORDER BY a.valid_from DESC LIMIT 1)
  ) AS product_code
  FROM slots s
),
actual_rates AS (
  SELECT sp.time, pr.unit_rate AS rate
  FROM slot_products sp
  JOIN product_rate pr
    ON pr.id = (
      SELECT pr2.id FROM product_rate pr2
      WHERE pr2.product_code = sp.product_code
        AND pr2.region = '${region}'
        AND pr2.valid_from <= sp.time
        AND sp.time < COALESCE(pr2.valid_to, '9999-12-31 23:59:59')
      ORDER BY pr2.valid_from DESC
      LIMIT 1
    )
),
projected_rates AS (
  SELECT tp.period_from AS time, tp.unit_rate AS rate
  FROM tariff_projection tp
  JOIN slot_products sp ON sp.time = tp.period_from
  WHERE tp.product_code = sp.product_code
    AND tp.region = '${region}'
    AND NOT EXISTS (
      SELECT 1 FROM product_rate pr3
      WHERE pr3.product_code = sp.product_code
        AND pr3.region = '${region}'
        AND pr3.valid_from <= sp.time
        AND sp.time < COALESCE(pr3.valid_to, '9999-12-31 23:59:59')
    )
),
forecast_rates AS (
  SELECT f.period_from AS time, f.forecast_unit_rate AS rate
  FROM agile_forecast f
  JOIN slot_products sp ON sp.time = f.period_from
  WHERE sp.product_code REGEXP '^AGILE-(FLEX-)?[0-9]'
    AND f.region = '${region}'
    AND f.period_from >= NOW()
    AND f.period_from < NOW() + INTERVAL 5 DAY
    AND NOT EXISTS (SELECT 1 FROM actual_rates a WHERE a.time = f.period_from)
),
rates AS (
  SELECT time, rate FROM actual_rates
  UNION ALL
  SELECT time, rate FROM projected_rates
  UNION ALL
  SELECT time, rate FROM forecast_rates
),
windows AS (
  SELECT
    time AS window_start,
    CASE WHEN COUNT(rate) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING) = 2
          AND MAX(time) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING) = time + INTERVAL 30 MINUTE
         THEN AVG(rate) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING) END AS avg_1h,
    CASE WHEN COUNT(rate) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND 3 FOLLOWING) = 4
          AND MAX(time) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND 3 FOLLOWING) = time + INTERVAL 90 MINUTE
         THEN AVG(rate) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND 3 FOLLOWING) END AS avg_2h,
    CASE WHEN COUNT(rate) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND 5 FOLLOWING) = 6
          AND MAX(time) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND 5 FOLLOWING) = time + INTERVAL 150 MINUTE
         THEN AVG(rate) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND 5 FOLLOWING) END AS avg_3h,
    CASE WHEN COUNT(rate) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND 7 FOLLOWING) = 8
          AND MAX(time) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND 7 FOLLOWING) = time + INTERVAL 210 MINUTE
         THEN AVG(rate) OVER (ORDER BY time ROWS BETWEEN CURRENT ROW AND 7 FOLLOWING) END AS avg_4h
  FROM rates
),
block_sizes AS (
  SELECT '1h' AS window_size, 1 AS hours
  UNION ALL SELECT '2h', 2
  UNION ALL SELECT '3h', 3
  UNION ALL SELECT '4h', 4
),
horizons AS (
  SELECT '24hrs' AS horizon, 1 AS days
  UNION ALL SELECT '3days', 3
  UNION ALL SELECT '5days', 5
),
blocks AS (
  SELECT b.window_size, b.hours, w.window_start,
    CASE b.hours WHEN 1 THEN w.avg_1h WHEN 2 THEN w.avg_2h WHEN 3 THEN w.avg_3h ELSE w.avg_4h END AS avg_rate
  FROM windows w
  CROSS JOIN block_sizes b
  WHERE w.window_start >= NOW()
),
ranked_blocks AS (
  SELECT bl.window_size, h.horizon, bl.window_start, bl.avg_rate,
    ROW_NUMBER() OVER (PARTITION BY bl.window_size, h.horizon ORDER BY bl.avg_rate ASC) AS cheapness
  FROM blocks bl
  JOIN horizons h ON bl.window_start + INTERVAL bl.hours HOUR <= NOW() + INTERVAL h.days DAY
  WHERE bl.avg_rate IS NOT NULL
),
cheapest AS (
  SELECT window_size, horizon, CONCAT(
     CASE WHEN avg_rate < 0 THEN '🔵' WHEN avg_rate < 10 THEN '🟢' WHEN avg_rate < 20 THEN '🟡' WHEN avg_rate <= 25 THEN '🟠' ELSE '🔴' END,
     ' ', DATE_FORMAT(CONVERT_TZ(window_start, 'UTC', '${timezone}'), '%a %H:%i'), ' · ', ROUND(avg_rate, 2), 'p'
   ) AS cell
  FROM ranked_blocks
  WHERE cheapness = 1
)
SELECT
  b.window_size,
  MAX(CASE WHEN c.horizon = '24hrs' THEN c.cell END) AS `24hrs`,
  MAX(CASE WHEN c.horizon = '3days' THEN c.cell END) AS `3days`,
  MAX(CASE WHEN c.horizon = '5days' THEN c.cell END) AS `5days`
FROM block_sizes b
LEFT JOIN cheapest c ON c.window_size = b.window_size
GROUP BY b.window_size
ORDER BY FIELD(b.window_size, '1h', '2h', '3h', '4h');```

### Consumption Heatmap (Hour-by-Hour, Last 45 Days) — heatmap, id 12

Title restored — the earlier accidental reset to Grafana's "Panel Title" placeholder is fixed; the panel's `timeFrom`/`timeShift` fix and query were unaffected throughout.

`Calculate from data: Off` plus a wide time-series shape (first field time-typed, one column per weekday) — Grafana's native Heatmap panel renders this as a categorical hour × weekday grid with no upgrade or transform needed. `time` is anchored to `TIMESTAMP(CURDATE())` purely to satisfy the time-typing requirement; only the hour-of-day component is meaningful. `timeFrom: "now/d"` + `timeShift: "0d/d"` pins the X axis to exactly today's 00:00–23:59, invariant of what time it actually is when the dashboard is viewed — the combination of both fields together is required; `timeFrom` alone always hardcodes the panel's end to literal "now", which is why this needed the two-field form rather than a single override. `period_from >= NOW() - INTERVAL 45 DAY`, not 90, per the retention-window cap above. `Y-Axis → Reverse: true` (Monday renders at the top). Field override applies the `kWh` custom unit via `cellValues.unit`. Colour scale `max: 1` caps saturation at 1 kWh (previously auto-scaled to the data's own max), so a single unusually high hour no longer washes out the contrast between every other cell; `gridPos.h` is now `16` (was `17`).

```sql
SELECT
  TIMESTAMP(CURDATE()) + INTERVAL HOUR(CONVERT_TZ(period_from, 'UTC', '${timezone}')) HOUR AS time,
  ROUND(AVG(CASE WHEN DAYNAME(CONVERT_TZ(period_from, 'UTC', '${timezone}')) = 'Monday'    THEN est_kwh END), 4) AS `Monday`,
  ROUND(AVG(CASE WHEN DAYNAME(CONVERT_TZ(period_from, 'UTC', '${timezone}')) = 'Tuesday'   THEN est_kwh END), 4) AS `Tuesday`,
  ROUND(AVG(CASE WHEN DAYNAME(CONVERT_TZ(period_from, 'UTC', '${timezone}')) = 'Wednesday' THEN est_kwh END), 4) AS `Wednesday`,
  ROUND(AVG(CASE WHEN DAYNAME(CONVERT_TZ(period_from, 'UTC', '${timezone}')) = 'Thursday'  THEN est_kwh END), 4) AS `Thursday`,
  ROUND(AVG(CASE WHEN DAYNAME(CONVERT_TZ(period_from, 'UTC', '${timezone}')) = 'Friday'    THEN est_kwh END), 4) AS `Friday`,
  ROUND(AVG(CASE WHEN DAYNAME(CONVERT_TZ(period_from, 'UTC', '${timezone}')) = 'Saturday'  THEN est_kwh END), 4) AS `Saturday`,
  ROUND(AVG(CASE WHEN DAYNAME(CONVERT_TZ(period_from, 'UTC', '${timezone}')) = 'Sunday'    THEN est_kwh END), 4) AS `Sunday`
FROM consumption
WHERE energy = 'E'
  AND period_from >= NOW() - INTERVAL 45 DAY
GROUP BY HOUR(CONVERT_TZ(period_from, 'UTC', '${timezone}'))
ORDER BY time;
```

### Daily Average Unit Price (Rolling 7-Day Window) — timeseries, id 14

The consumption-weighted effective price paid per kWh each day — `SUM(est_kwh * unit_rate) / SUM(est_kwh)`, excluding standing charge — rather than a flat average of the tariff's own rate across the day's half-hours. On a tariff such as Agile (half-hourly) or Go (two windows a day) the unit rate varies through the day, so weighting by when power was actually used (not just what the tariff charged in the abstract) is what makes this track what Daily Average Cost is actually built from. Electricity only, matching its two siblings. `timeFrom: 45d`. Same day-completeness guard and 7-day `ROWS BETWEEN 6 PRECEDING AND CURRENT ROW` rolling average as those siblings. Reuses the Agile Prices panel's `p/kwh` unit and threshold bands (light-blue below 0p, green from 0p, yellow from 10p, semi-dark-orange from 20p, red from 25p) for visual consistency, since both show the same kind of value. `min: -1` allows the light-blue sub-zero band to render, matching Agile's own negative-pricing case. `decimals: 2`. Unlike its siblings, this panel divides by a data-dependent value (`SUM(est_kwh)`) rather than a constant — the day-completeness guard only checks row *count*, not that consumption summed to non-zero, so any day whose half-hourly readings net to exactly zero (meter offline with no draw, or — in principle — export exactly offsetting import) would divide by zero. MariaDB returns `NULL` for that day rather than erroring, and the rolling-average window function skips it, so this degrades to a gap in the line rather than a crash or a wrong value.

Added as the leftmost of the three in the grid row (`x: 10, w: 5`), followed by Cost then Usage — left-to-right order Unit Price → Cost → Usage, per explicit direction during the grill session. Required narrowing both siblings to make room — Cost is now `x: 15, w: 4` (was `x: 10, w: 7`) and Usage is now `x: 19, w: 5` (was `x: 17, w: 7`) — so all three still exactly fill the row's `x: 10` to `x: 24` span.

```sql
SELECT
  d AS time,
  ROUND(AVG(daily_unit_price) OVER (ORDER BY d ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2) AS rolling_avg_unit_price_p
FROM (
  SELECT
    DATE(CONVERT_TZ(c.period_from, 'UTC', '${timezone}')) AS d,
    SUM(c.est_kwh * pr.unit_rate) / SUM(c.est_kwh) AS daily_unit_price
  FROM consumption c
  JOIN agreement a
    ON a.energy = c.energy
   AND c.period_from >= a.valid_from
   AND c.period_from < COALESCE(a.valid_to, '9999-12-31 23:59:59')
  JOIN product_rate pr
    ON pr.id = (
      SELECT pr2.id FROM product_rate pr2
      WHERE pr2.product_code = a.product_code
        AND pr2.region = '${region}'
        AND pr2.valid_from <= c.period_from
        AND c.period_from < COALESCE(pr2.valid_to, '9999-12-31 23:59:59')
      ORDER BY pr2.valid_from DESC
      LIMIT 1
    )
  WHERE c.energy = 'E'
    AND c.period_from >= NOW() - INTERVAL 45 DAY
  GROUP BY DATE(CONVERT_TZ(c.period_from, 'UTC', '${timezone}'))
  HAVING COUNT(*) = TIMESTAMPDIFF(MINUTE,
    CONVERT_TZ(CAST(d AS DATETIME), '${timezone}', 'UTC'),
    CONVERT_TZ(CAST(d + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
  ) / 30
) daily
ORDER BY d;
```

### Daily Average Cost (Rolling 7-Day Window) — timeseries, id 8

Same query as earlier drafts' "Daily Average Cost — 7-Day Rolling Average, 45 Days", renamed. `timeFrom: 45d`. Thresholds match the blue/green/yellow/orange/red band style: blue below £1, green from £1 (moved from £2), yellow from £3, orange from £4, red from £5. `min: -1` (was `0`).

```sql
SELECT
  d AS time,
  ROUND(AVG(daily_cost) OVER (ORDER BY d ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2) AS rolling_avg_cost_gbp
FROM (
  SELECT
    DATE(CONVERT_TZ(c.period_from, 'UTC', '${timezone}')) AS d,
    (SUM(c.est_kwh * pr.unit_rate) + COALESCE(MAX(CASE WHEN TIME(CONVERT_TZ(c.period_from, 'UTC', '${timezone}')) = '12:00:00' THEN pr.standing_charge END), MAX(pr.standing_charge))) / 100 AS daily_cost
  FROM consumption c
  JOIN agreement a
    ON a.energy = c.energy
   AND c.period_from >= a.valid_from
   AND c.period_from < COALESCE(a.valid_to, '9999-12-31 23:59:59')
  JOIN product_rate pr
    ON pr.id = (
      SELECT pr2.id FROM product_rate pr2
      WHERE pr2.product_code = a.product_code
        AND pr2.region = '${region}'
        AND pr2.valid_from <= c.period_from
        AND c.period_from < COALESCE(pr2.valid_to, '9999-12-31 23:59:59')
      ORDER BY pr2.valid_from DESC
      LIMIT 1
    )
  WHERE c.energy = 'E'
    AND c.period_from >= NOW() - INTERVAL 45 DAY
  GROUP BY DATE(CONVERT_TZ(c.period_from, 'UTC', '${timezone}'))
  HAVING COUNT(*) = TIMESTAMPDIFF(MINUTE,
    CONVERT_TZ(CAST(d AS DATETIME), '${timezone}', 'UTC'),
    CONVERT_TZ(CAST(d + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
  ) / 30
) daily
ORDER BY d;
```

### Daily Average Usage (Rolling 7-Day Window) — timeseries, id 7

Same query as earlier drafts' "Daily Average Usage — 7-Day Rolling Average, 45 Days", renamed. `timeFrom: 45d`.

```sql
SELECT
  d AS time,
  ROUND(AVG(daily_kwh) OVER (ORDER BY d ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 3) AS rolling_avg_kwh
FROM (
  SELECT DATE(CONVERT_TZ(period_from, 'UTC', '${timezone}')) AS d, SUM(est_kwh) AS daily_kwh
  FROM consumption
  WHERE energy = 'E'
    AND period_from >= NOW() - INTERVAL 45 DAY
  GROUP BY DATE(CONVERT_TZ(period_from, 'UTC', '${timezone}'))
  HAVING COUNT(*) = TIMESTAMPDIFF(MINUTE,
    CONVERT_TZ(CAST(d AS DATETIME), '${timezone}', 'UTC'),
    CONVERT_TZ(CAST(d + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
  ) / 30
) daily
ORDER BY d;
```

---

## Row 4 — Yearly Comparison

Reads from `daily_consumption_summary`, exempt from the 45-day retention cap above. Electricity only — the gas variants of both panels documented in earlier drafts of this file are not present in the current dashboard (see the callout below).

### Monthly Total Consumption — timeseries, id 9

`timeFrom: 400d`. Legend hidden (`showLegend: false`) — briefly shown for a while, reverted back since (single-series panel, the legend added little). Anchored to the first of the month 11 months ago via date arithmetic (`DATE_SUB(date, INTERVAL DAYOFMONTH(date) - 1 DAY)`), not `DATE_FORMAT(...)`, so `time` stays a real `DATE`-typed column rather than a string. `timeFrom` is deliberately wider than the query's nominal ~365-day lookback: the true span between "now" and the oldest bucket's timestamp ranges from ~334 to ~366 days depending on where in the current month "now" falls and whether the 12-month window crosses a leap day — a plain `365d` override left zero margin against that leap-year case and clipped the oldest bar.

```sql
SELECT
  DATE_SUB(date, INTERVAL DAYOFMONTH(date) - 1 DAY) AS time,
  SUM(total_kwh) AS monthly_kwh
FROM daily_consumption_summary
WHERE energy = 'E'
  AND date >= DATE_FORMAT(CURDATE() - INTERVAL 11 MONTH, '%Y-%m-01')
GROUP BY DATE_SUB(date, INTERVAL DAYOFMONTH(date) - 1 DAY)
ORDER BY time;
```

### Consumption Year-on-Year Percentage Change — timeseries, id 10

`timeFrom: 400d`, widened from `365d` for the same reason as the Monthly Total Consumption panel above — the query's 52-53 ISO-week lookback isn't a fixed day count either, and the narrower value could clip the oldest week. Groups by ISO week (`YEARWEEK(date, 3)`) and compares each week against the same ISO week one year prior, with a week-53 fallback and a completeness guard excluding any week without all 7 days present. Field overrides rename `yoy_pct_change` → "Week-by-Week Percentage Change" and `yoy_pct_change_4wk_avg` → "Rolling Average" (rendered as a zero-fill yellow line). Legend: table mode, shown on the right (unlike most other panels on this dashboard, which hide their legend).

```sql
WITH weekly AS (
  SELECT YEARWEEK(date, 3) AS yearweek, SUM(total_kwh) AS weekly_kwh
  FROM daily_consumption_summary
  WHERE energy = 'E'
  GROUP BY YEARWEEK(date, 3)
  HAVING COUNT(*) = 7
),
target AS (
  SELECT
    yearweek,
    weekly_kwh AS this_year_kwh,
    CASE
      WHEN MOD(yearweek, 100) = 53
       AND (yearweek - 100) NOT IN (SELECT yearweek FROM weekly)
      THEN (yearweek - 100) - 1
      ELSE yearweek - 100
    END AS comparator_yearweek
  FROM weekly
  WHERE yearweek >= YEARWEEK(CURDATE() - INTERVAL 52 WEEK, 3)
)
SELECT
  STR_TO_DATE(CONCAT(t.yearweek, ' Monday'), '%x%v %W') AS time,
  ROUND((t.this_year_kwh - c.weekly_kwh) / NULLIF(c.weekly_kwh, 0) * 100, 2) AS yoy_pct_change,
  ROUND(
    AVG((t.this_year_kwh - c.weekly_kwh) / NULLIF(c.weekly_kwh, 0) * 100)
      OVER (ORDER BY t.yearweek ROWS BETWEEN 3 PRECEDING AND CURRENT ROW),
    2
  ) AS yoy_pct_change_4wk_avg
FROM target t
LEFT JOIN weekly c ON c.yearweek = t.comparator_yearweek
ORDER BY t.yearweek;
```

## Row 6 — Fuel Threshold

These panels come from the hypervolt-agile charging scheduler's own dashboard. The `fuel_price_threshold` table is created and written by hypervolt-agile in the same `home_monitoring` database, not by this repo's apps, so until it has recorded a row the gauge reads "no price recorded" and the chart shows no data.

### Fuel price (latest) — gauge, id 20

The most recent recorded fuel price in pence per litre (`p/L`), as a gauge from 100 to 200 with threshold colours green, yellow from 120, orange from 140 and red from 160.

```sql
SELECT fuel_price_pence_per_litre AS fuel_price_p_per_litre
FROM fuel_price_threshold
WHERE fuel_price_pence_per_litre IS NOT NULL
ORDER BY recorded_at DESC LIMIT 1;
```

### Fuel price vs effective charging limit — timeseries, id 21

Every recorded fuel price and effective charging limit over the last 45 days (`timeFrom: 45d`, not the dashboard's shared range), so the two can be compared as they move. The fuel price is in `p/L` and the limit is in pence per kWh including VAT.

```sql
SELECT recorded_at AS time,
  fuel_price_pence_per_litre AS fuel_price_p_per_litre,
  effective_limit_incl_vat AS effective_limit_p_per_kwh
FROM fuel_price_threshold
WHERE $__timeFilter(recorded_at)
ORDER BY recorded_at;
```

---

## Panels documented in earlier drafts that are no longer on the dashboard

**Flagging rather than silently deleting the history** — worth confirming this is intentional next time the dashboard's touched:

- **Row 3 — Gas**: Gas Consumption and Gas Cost panels, plus the gas variants of Monthly Total Consumption and Year-on-Year Change. None appear in the current JSON export.
- **Row 5 — Health**: Last Successful Run per Job and AgilePredict/Kraken Reachability. Neither appears in the current JSON export.
- **Latest Consumption** (id 4: half-hourly kWh and cost on a dual axis, 48 hours): removed by hand in the live Grafana dashboard in November 2026, so it is absent from the export and from this file.
- **Standing Charge vs Unit-Rate Cost Split** (stacked bar) and the old **Cheapest N-Hour Window Today/Tomorrow** table (superseded by the new Cheapest Time Window matrix above) — also absent.

If gas monitoring and the health-check panels are meant to still be live, they may exist on a different dashboard than the one exported here (`data/grafana/electricity-dashboard.json`, titled **"pi-desktop: electricity-monitoring"**).

# Project unpublished current-tariff rates and show them on the dashboard

## Problem Statement

The account moved from Agile to Octopus Go at 2026-11-05 00:00 UTC. Since then the Cheapest Time Window shows only "Thu … · 34.53p" in every cell. Octopus publishes Go rates only to the end of the current day: the latest published slot ends at 00:30 tonight, even when the API is asked for a later range. The cheap 9.40p window (00:30–05:30 local) for tonight is not published yet. The Agile forecast fills only Agile half-hours, so every half-hour the table can see is at the peak rate.

The dashboard also has no chart of the current tariff's prices. The Agile Prices panel always shows Agile, by design, as a comparison.

## Solution

octopus-app writes a **Tariff Projection** for the current non-Agile electricity tariff on every pricing refresh. Every half-hour Octopus has not yet published, out to six days ahead, gets the rate at the same local clock time on the latest fully published day ([ADR-0032](../adr/0032-unpublished-current-tariff-rates-are-projected-into-a-table.md)).

Both panels price each half-hour from the agreement covering it, taking the first source that has a rate:

1. the published `product_rate`
2. the Tariff Projection (non-Agile tariffs)
3. the Agile forecast (Agile tariffs)

A new **Current Tariff Prices** time-series panel shows those half-hourly prices across the dashboard's range (now-3h to now+120h by default, capped to its last 7 days). Published slots are drawn as a solid line, projected or forecast slots as a dashed one. It sits at the top of the right-hand column, above the Agile Prices panel. The Cheapest Time Window keeps its 24hrs / 3days / 5days columns, so on Go its 24hrs column now offers tonight's 9.40p window.

## User Stories

1. As the householder on Octopus Go, I want the Cheapest Time Window to include tonight's cheap window before Octopus publishes it, so that I can plan the car charge and appliances.
2. As the householder, I want a chart of my current tariff's prices for the next 24 hours and beyond, so that I can see at a glance when power is cheap.
3. As the householder, I want projected prices drawn differently from published ones, so that I know which prices Octopus has confirmed.
4. As the householder, I want the chart to follow whatever tariff I am on, so that switching back to Agile shows Agile actual and forecast prices without editing the dashboard.
5. As the householder, I want the Agile Prices panel to stay as it is, so that I can still compare Go with Agile.
6. As the householder, I want published prices always to replace projected ones once Octopus publishes them, so that the dashboard never shows a guess when the real price is known.
7. As the householder, I want the projection to stay right across a clock change, so that the cheap window stays at 00:30–05:30 local time in summer and winter.
8. As the householder, I want the dashboard to show only published prices when no projection can be made, rather than wrong prices, so that a data gap never shows as cheap power.
9. As the maintainer, I want the projection rule to have one implementation, shared with the Cost Forecast, so that the two never disagree.
10. As the maintainer, I want a projection left over from an earlier tariff to disappear on the next refresh, so that a tariff change never leaves stale projected slots behind.

## Implementation Decisions

- **New table `tariff_projection`** (created by Schema Sync, no manual DDL). Columns: `id` (string primary key scoped by product, region and slot start), `product_code`, `region`, `period_from`, `period_to` (half-hour slots, UTC), `unit_rate` (`Numeric(9, 6)`, like `product_rate`) and `projected_at`. There is an index on `(product_code, region, period_from)` for the panels' per-slot lookups. All values are UTC by the existing convention.
- **When it runs**: at the end of `PricingRetriever.refresh`, after the own-product and comparison rate syncs, so it always projects from the rates just stored. It is part of the `pricing_refresh` job and needs no new job or `job_run` name.
- **What it projects**:
  - The product of the electricity agreement in force at `as_of`, when that product is not Agile (same `AGILE-` / `AGILE-FLEX-` product-code test the dashboard uses).
  - Half-hour slots from the end of the product's published coverage (the first half-hour at or after `as_of` with no published rate) to `as_of` + 6 days. That covers the 120 h dashboard range plus margin between hourly refreshes.
  - Slots Octopus has published are never projected.
- **Projection rule**: reuse the Cost Forecast's existing per-slot rule in `time_of_use_cost`, currently the private `_SlotRates`: the published rate if any, else the rate at the same local clock time on the latest fully published local day, searched back up to the existing lookback. It becomes a shared, public helper. Its search start, today named after the billing period end, is generalised to "the last day needing a rate", so the projection and the Cost Forecast call the same code.
- **Full rewrite in one transaction** on every run, using the client's existing replace-all-rows helper. A tariff change, a switch to Agile (writes zero rows) or a newly published day replaces every old projected slot. No pruning job change is needed.
- **No fully published day in the lookback**: write nothing, which leaves the table empty after the rewrite, and log a warning naming the product. The pricing refresh itself still succeeds; the projection must never fail the rate sync.
- **No current electricity agreement**: write nothing (empty table) and log at info.
- **Cheapest Time Window (panel 13)**: a `projected_rates` CTE joins `slot_products` to `tariff_projection` on product, region and slot start, excluding slots `actual_rates` already has. `rates` becomes the union of actual, projected and forecast rates. Everything else in the query is unchanged: horizons, colour bands, the full-block rule and the blank title.
- **Cheapest Time Window memory**: with the projection, `rates` on Go grows from about 50 published half-hours to about 240. The query's twelve scalar subqueries each re-evaluate the `windows` CTE, which took the server's memory from about +120 MiB to about +380 MiB. That was measured on a live-configured copy on the Pi, against the database's 512 MB cap; a heavier variant OOM-killed the live database during development. The final select therefore makes one pass over `windows`: it unpivots the four block sizes, ranks each size within each horizon with `ROW_NUMBER()`, and pivots back to the same 4 × 3 table. The result is the same: every block size row is still returned, a cell with no full block is still empty, and ties are still broken arbitrarily. A projected slot is dropped by probing the indexed `product_rate` table, not the `actual_rates` CTE. Both panel queries must stay well under the cap: check peak server memory on a live-configured copy, never on the live database.
- **New panel "Current Tariff Prices" (id 23, timeseries)**:
  - Gets the same half-hourly per-slot pricing as the Cheapest Time Window: the `slots`, `slot_products` and source precedence logic, with slots generated from `$__timeFrom()` to `$__timeTo()`, capped to the last 7 days of that range. There is one recursive row per half-hour, so a wide range such as "Last 30 days" would pass MariaDB's recursion limit and build the per-slot subqueries on the memory-capped Pi.
  - Returns wide columns `time`, `published`, `projected` (Tariff Projection) and `forecast` (Agile forecast), at most one non-null per row. These are fixed field names for the overrides to match; a long `series` column would leave Grafana to name the split fields. A `stepAfter` line draws nothing after its last point, so the query handles the edges explicitly:
    - There is one row per half-hour of the slot timeline. A half-hour with no rate is an all-null row, so the line breaks there instead of stepping across the gap at the previous price, even for a single half-hour.
    - Each run of one source is closed by an extra row at the last second of its final half-hour (start + 29:59), carrying that half-hour's rate in its own column. A run ends when the next half-hour has another source or no rate, or when the timeline ends. The closing row never shares a timestamp with a half-hour, so a single-half-hour gap or island of another source still has a null between runs.
  - Drawn as lines, coloured by the price bands. Field overrides draw `projected` and `forecast` dashed. The step interpolation is `stepAfter`, so multi-hour Go windows draw flat.
  - Unit `p/kwh`, with the same threshold lines and price bands as the Agile Prices panel.
  - Grid position x 14, y 0, w 10, h 9, on the live dashboard's layout as exported from Grafana on 2026-11-05. That layout moved Current Tariff from a full-width banner to a small stat and renamed the fuel panels. The JSON is rebased on that export: apart from grid positions, panel 13's query and the annotation filters, every other panel stays exactly as exported. The Agile Prices and Cheapest Time Window panels move down by 9, and any overlapping panel below moves down to keep the grid free of overlaps.
- **Annotations**: the `+24h` and `0h` markers' panel filter `ids` becomes `[3, 23]`.
- **Reference doc**: `data/grafana/mariadb/electricity-queries.md` gets a section for panel 23. The Cheapest Time Window section and its query gain the projection, and the schema list gains `tariff_projection`. The doc and the JSON change together, as the doc requires; bump the dashboard `version`.

## Testing Decisions

- Tests check behaviour at the highest existing seam. Assert on the rows written, not on how they were computed.
- **Projection seam: `PricingRetriever.refresh(as_of)`**. It runs against the SQLite-backed `mariadb_client` fixture with the Octopus API mocked via `responses`, following `test_pricing_ended_agreement.py` and the helpers in `test_pricing_retrieval.py`. Scenarios:
  - On Go with rates published to 00:30 tomorrow (GMT), tomorrow's 00:30–05:30 slots are projected at the off-peak rate and the rest at the peak rate, out to `as_of` + 6 days.
  - No slot Octopus has published is projected.
  - Across a clock change, the cheap window stays at 00:30–05:30 local time (UTC 23:30–04:30 in BST).
  - On Agile, nothing is projected.
  - With no fully published day in the lookback, nothing is written and a warning is logged.
  - A projection from an earlier refresh, or an earlier product, is replaced by the next refresh.
  - The Cost Forecast's existing Go tests (`test_cost_forecast_go.py`) still pass unchanged, proving the shared rule did not move.
- **Dashboard seam: JSON and SQL string checks** in `scripts/tests/test_grafana_electricity_tariff_panels.py`, as done today:
  - Panel 23: type, title, grid position, reads `tariff_projection`, `product_rate` and `agile_forecast`, and its precedence `NOT EXISTS` checks.
  - Panel 23: dashed `projected` and `forecast` overrides, and the annotation filters include 23.
  - The Cheapest Time Window reads `tariff_projection`.
  - The reference doc carries the same queries as the dashboard, with panel 23 added to that existing test.
  - `apps/octopus-app/tests/test_grafana_dashboards.py` covers the `dataset`/datasource placeholder rules for the new panel automatically.

## Out of Scope

- Projecting gas rates or any product other than the current electricity agreement's, including comparison products and future-dated agreements.
- Projecting Agile beyond the Agile forecast's horizon.
- Marking projected windows in Cheapest Time Window cells.
- Changing the Agile Prices panel, the dashboard time range or the Cheapest Time Window horizons.
- Deploying to the Pi and re-importing the dashboard (done after merge, as usual).

## Further Notes

- Root cause, observed on the live database at 2026-11-05 09:34 UTC: the latest `product_rate` row for `GO-VAR-22-10-14` in region C ends at `2026-11-06 00:30` UTC. The public API returns the same three windows for any `period_to`.
- Deploy order matters: re-import the dashboard only after the new octopus-app image has started, because Schema Sync creates `tariff_projection` on startup. Before that, panels 13 and 23 would fail on a missing table. Watchtower pulls images at 03:00 UTC; pull by hand to deploy sooner.

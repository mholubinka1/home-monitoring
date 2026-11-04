# Merge the hypervolt-agile fuel threshold panels into the electricity dashboard

## Problem Statement

The hypervolt-agile charging scheduler records its effective charging price limit to the `fuel_price_threshold` table in the `home_monitoring` database, and ships its own Grafana dashboard (`fuel-threshold.json`) to show it. That dashboard has to be imported and opened separately, so the limit cannot be seen next to the Agile prices and the consumption and cost panels it relates to.

## Solution

One electricity dashboard shows everything. The Agile Prices panel gains the dashed "Effective charging limit" line from the fuel dashboard, and two new panels, "Fuel price (latest)" and "Fuel price vs effective charging limit", sit alongside the existing panels. The separate fuel dashboard is no longer needed for viewing.

## User Stories

1. As the household's energy monitor, I want the effective charging limit drawn on the Agile Prices chart, so that I can see which half-hours fall under it.
2. As the household's energy monitor, I want the latest fuel price shown as a gauge on the electricity dashboard, so that I can see the current price without opening another dashboard.
3. As the household's energy monitor, I want fuel price and the effective limit charted together, so that I can see how they have moved against each other.
4. As the dashboard maintainer, I want the merged dashboard to import cleanly into Grafana, so that no panel id, variable or data source input clashes.
5. As the dashboard maintainer, I want the existing electricity panels, variables and annotations unchanged, so that the merge does not alter what already works.
6. As the dashboard maintainer, I want the new panels documented in the reference queries file, so that it stays an accurate record of the dashboard.

## Implementation Decisions

- The base is the current live electricity dashboard export, supplied by the user from Grafana, not the older committed copy.
- The Agile Prices panel (id 3) is replaced by the hypervolt-agile version: same position in the data model, plus query B (effective charging limit) and its override (dashed, purple, hidden from legend and tooltip). The panel keeps id 3 so the `+24h` and `0h` annotation markers, which are filtered to panel id 3, still apply.
- Fuel price (latest) (id 20) and Fuel price vs effective charging limit (id 21) are added from `fuel-threshold.json`. Ids do not clash with the existing ids.
- Both dashboards use the same `DS_MYSQL` input, the same `region` variable and the same time range and refresh, so no new inputs or variables are needed. The `__requires` list gains the gauge panel type.
- Layout: the Agile Prices panel keeps its current slot and width, and the two fuel panels go in a new row at the bottom so existing panels do not move.
- The `fuel_price_threshold` table is created and written by hypervolt-agile in the same database. No schema or app change is made in this repo. Until hypervolt-agile has recorded a row, the new panels and the limit line show no data.
- The dashboard title and uid stay those of the electricity dashboard. The dashboard `version` is not hand-edited beyond what the export provides.
- `data/grafana/mariadb/electricity-queries.md` gains entries for the two new panels and the new Agile Prices query, in the file's existing per-panel format.

## Testing Decisions

- A good test reads the dashboard JSON and asserts its observable structure, not how it was built.
- One seam: the parsed `data/grafana/electricity-dashboard.json`, in a new test file under `scripts/tests/`, in the style of `test_grafana_timezone.py`. A second check on the same seam reads `electricity-queries.md` and asserts it documents each of the new panels.
- Assertions: panel ids are unique; panels 20 and 21 exist with their titles and read `fuel_price_threshold`; panel 3 has a query B and the "Effective charging limit" override; no panel overlaps another in `gridPos` and none exceeds 24 columns; the existing panel titles are all still present; `gauge` is in `__requires`.
- The existing `test_grafana_timezone.py` must still pass.

## Out of Scope

- Changing hypervolt-agile, its table or its recorder.
- Deleting `fuel-threshold.json` from hypervolt-agile.
- Restyling or repositioning the existing electricity panels.
- Importing the dashboard into Grafana (the user does that).

## Further Notes

- The user has updated the electricity dashboard in the main checkout (uncommitted at 11:24 on 2026-11-04). That file, not the committed one, is the merge base.
- `init-agent-docs` rewrote `.agent-docs/context.md`; that change was reverted to allow the branch switch, and a backup is in the session scratchpad. It is not part of this change.

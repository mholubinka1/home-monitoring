# Gas Dashboard Panel Cleanup

## Problem Statement

The gas/heating dashboard (`data/grafana/gas-heating-dashboard.json`) has 13 panels and, on review (2026-11-02), is too busy. Several panels do not support a decision the household makes: the hour-by-weekday gas heatmap, the monthly year-on-year percentage, the "saving from a thermostat 1 C lower" estimate, and four panels built on the daily heating model (the gas-against-effective-temperature scatter, Where the Gas Went, and both Heating Efficiency charts) that the user found hard to read. The outdoor temperature band also carries sunshine hours and solar radiation that are not used.

## Solution

Remove the seven panels and the sunshine/solar series from the dashboard and its query reference, drop the dashboard variables only those panels used, and close the gaps in the layout. The six remaining panels are unchanged in behaviour. The daily heating model keeps running in octopus-app because the cost forecast reads it; deleting its now-unread backend code is a separate follow-up change (workstream A2). Hive-app keeps recording sunshine and radiation.

## User Stories

1. As the household reviewing the dashboard, I want only the panels that support a decision, so that the dashboard is quick to read.
2. As the household, I want the outdoor temperature band to show only temperatures, so that unused sunshine and radiation series do not clutter it.
3. As a maintainer, I want the query reference to describe exactly the panels in the JSON, so that the two files stay in sync.

## Implementation Decisions

- Remove panels 7 (Monthly Gas Year-on-Year Change), 8 (Gas Heatmap), 9 (Heating Model scatter), 10 (Where the Gas Went), 11 (Heating Efficiency, 7-day rolling), 12 (Heating Efficiency, This Year and Last) and 13 (Saving from a Thermostat 1 C Lower).
- Panel 5 is retitled "Daily Outdoor Temperature Band" and its query stops selecting sunshine hours and radiation; its description and overrides lose them too.
- Remove the variables `model_summary`, `all_weeks` and `yoy_summary`; every remaining variable is still used.
- Layout: rows keep their order (temperatures, daily gas, boost and minutes below target side by side, outdoor band, billing period progress); the billing period progress gauge widens to the full 24 columns.
- `gas-heating-queries.md`: delete the removed panels' sections and the "Row 3 — Heating model" heading, update panel 5's section and the variables list.
- Later reworks of the remaining panels (outdoor band as lines with forecast, hourly heating cost, comfort temperature) are separate workstreams and out of scope.

## Testing Decisions

- `apps/octopus-app/tests/test_grafana_dashboards.py` is the seam: it loads the dashboard JSON. Tests for the removed panels and variables are deleted; counts that included the removed panels are corrected (two daily gas queries, not four); new tests assert the removed panels and variables are absent, the band has no sunshine or radiation series, and the panels tile the grid with no gaps or overlaps.
- Check the re-laid-out dashboard once at the end of all waves, per the agreed single end-of-work review.

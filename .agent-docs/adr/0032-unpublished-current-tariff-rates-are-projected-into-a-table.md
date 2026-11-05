# Unpublished current-tariff rates are projected by octopus-app into a table

Status: accepted

Octopus publishes Octopus Go rates only to the end of the current day, so after the move from Agile to Go on 2026-11-05 the Cheapest Time Window could see nothing past 00:30 and missed every cheap overnight window. On each pricing refresh octopus-app now writes a Tariff Projection for the current non-Agile electricity tariff into a `tariff_projection` table: half-hourly rates from the end of the last published rate to six days ahead, each taken from the same local clock time on the latest fully published day (the rule the Cost Forecast already uses for Go). The dashboard reads published `product_rate` rows first, then `tariff_projection`, then `agile_forecast` for Agile, so a published rate always wins once it appears.

## Considered options

- **Compute the projection in each panel's SQL**: rejected. The rule would be written twice in two long queries, the clock-change arithmetic is awkward in SQL, and the dashboard SQL is only string-checked by tests, never run.
- **Store projected rows in `product_rate` with a flag**: rejected. Every cost query reads `product_rate` and would have to exclude projected rows, or it would bill prices Octopus never published.

## Consequences

- The table is rewritten in full on every refresh, so a tariff change or a newly published day replaces stale projected slots within an hour.
- A Go price change reaches the projected slots only once a full local day at the new price is published; until then they repeat the old price.

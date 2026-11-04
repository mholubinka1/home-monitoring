# Octopus Go tariff support

## Problem Statement

The account has moved from Agile to Octopus Go (`GO-VAR-22-10-14`). The app and dashboard assume Agile or a single flat rate. Go is not recognised as a Tariff Type, so the Cost Forecast prices the rest of the Billing Period at whichever of Go's two rates happens to be in force when the job runs. The Cheapest Time Window panel mixes Agile forecast rates into a Go account and mis-averages Go's multi-hour rate windows. Nothing on the dashboard says which tariff the account is on.

## Solution

Recognise Go as a Tariff Type, project its cost from the rate the household actually pays per kWh, make the tariff-dependent panels follow the account's current tariff, and add a panel that names the current tariff. The Agile Prices panel keeps showing Agile prices and the Agile forecast whatever the account is on.

## User Stories

1. As an account holder on Go, I want the Cost Forecast to project the rest of the Billing Period at the rate I really pay per kWh, so that the projected spend reflects my cheap-window usage.
2. As an account holder on Go at the start of a Billing Period, I want a sensible projection before any complete day exists, so that the forecast is not blank or wrong on day one.
3. As an account holder, I want the Agile Prices panel to keep showing Agile actual and forecast prices while I am on Go, so that I can judge whether to switch back.
4. As an account holder on Go, I want the Cheapest Time Window panel to use my tariff's rates, so that it shows when my cheap window is.
5. As an account holder on Agile, I want the Cheapest Time Window panel to behave as before, including the Agile forecast beyond published rates, so that nothing regresses.
6. As an account holder, I want a panel naming my current tariff, so that I can see at a glance which tariff the dashboard is reflecting.
7. As a maintainer, I want the other cost panels to keep working on Go, so that no further changes are needed.

## Implementation Decisions

- New Tariff Type `go`, detected from a tariff code containing `-GO-`. Defined in the domain glossary as **Octopus Go**.
- Go publishes two multi-hour product-rate windows per day (cheap 00:30–05:30 local time, peak otherwise). The existing generic rate fetch and storage already handle this, so no pricing-pipeline or schema change is needed.
- Cost Forecast: for `go`, the remaining consumption is priced at the effective unit rate paid per kWh over real (not gap-filled, not today) elapsed days, i.e. (day cost less standing charge) / kWh. With no such day, the time-weighted average of today's published windows is used. Other tariff types are unchanged.
- Agile Prices panel (id 3): the actual series and the forecast de-duplication read the Agile product (the account's own if it is Agile, otherwise the newest Agile product in the catalogue, whose comparison rates are already synced), not the account's current product.
- Cheapest Time Window panel (id 13): rates are expanded to half-hourly slots from whichever product-rate row covers each slot, for the account's current product. The Agile forecast is used only when the current product is Agile.
- Panels joining consumption to product rates by "latest rate at or before the slot" already work on Go and are unchanged.
- New Stat panel "Current Tariff" (id 22), full width at the top of the electricity dashboard, showing the current electricity agreement's product display name, falling back to the tariff code. Existing panels shift down to make room. `stat` is added to the dashboard's required panel types.
- Dashboard reference doc (`electricity-queries.md`) is updated alongside the JSON.

## Testing Decisions

- Test external behaviour at existing seams: `CostForecastRetriever.refresh` against the SQLite-backed client with mocked Kraken HTTP, tariff-code to Tariff Type mapping, and the dashboard JSON tests under `scripts/tests`.
- Cost forecast prior art: `test_cost_forecast_retriever.py` (fixed and Agile tariffs). New tests in `test_cost_forecast_go.py` cover the effective-rate projection with skewed consumption and the day-average fallback.
- Dashboard prior art: `scripts/tests/test_grafana_fuel_threshold_panels.py`. New tests assert the Current Tariff panel exists, reads the current agreement, `stat` is required, panels still do not overlap, and the Agile Prices and Cheapest Time Window queries are tariff-scoped as specified.
- The dashboard SQL cannot be executed locally (no MariaDB); this is stated in the PR and needs a manual check in Grafana.

## Out of Scope

- Go-specific panels (e.g. cheap-window share of usage).
- Forecasting Go rates beyond what Octopus publishes.
- Gas tariffs, the gas dashboard, and Economy 7 handling.
- Rewriting the non-compliant glossary format.

## Further Notes

- Go projection assumes the household's recent peak/off-peak usage mix continues.
- The Cheapest Time Window on Go can only look as far ahead as Octopus has published rates.

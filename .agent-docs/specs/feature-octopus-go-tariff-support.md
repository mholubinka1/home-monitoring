# Octopus Go tariff support

## Problem Statement

The account has moved from Agile to Octopus Go (`GO-VAR-22-10-14`). The app and dashboard assume Agile or a single flat rate. Go is not recognised as a Tariff Type, so the Cost Forecast prices the rest of the Billing Period at whichever of Go's two rates happens to be in force when the job runs. The Cheapest Time Window panel mixes Agile forecast rates into a Go account and mis-averages Go's multi-hour rate windows. Nothing on the dashboard says which tariff the account is on.

## Solution

Recognise Go as a Tariff Type, price every half-hour by the agreement and rate in force at that moment (so a change of tariff is handled wherever it falls), project the Go cost slot by slot from the household's own time-of-day usage, make the tariff-dependent panels follow the account's current tariff, and add a panel that names the current tariff. The Agile Prices panel keeps showing Agile prices and the Agile forecast whatever the account is on.

## User Stories

1. As an account holder on Go, I want the Cost Forecast to project the rest of the Billing Period at the rate I really pay per kWh, so that the projected spend reflects my cheap-window usage.
2. As an account holder on Go at the start of a Billing Period, I want a sensible projection before any complete day exists, so that the forecast is not blank or wrong on day one.
3. As an account holder, I want the Agile Prices panel to keep showing Agile actual and forecast prices while I am on Go, so that I can judge whether to switch back.
4. As an account holder on Go, I want the Cheapest Time Window panel to use my tariff's rates, so that it shows when my cheap window is.
5. As an account holder on Agile, I want the Cheapest Time Window panel to behave as before, including the Agile forecast beyond published rates, so that nothing regresses.
6. As an account holder, I want a panel naming my current tariff, so that I can see at a glance which tariff the dashboard is reflecting.
7. As an account holder, I want every cost panel to price each half-hour with the rate row whose own validity covers it, so that a missing or late rate is never silently replaced by an older one.
8. As an account holder, I want a day on which I changed tariff to take its standing charge from the agreement in force at local midday, so that it matches how gap days are already priced.
9. As an account holder, I want the Cheapest Time Window to price each future slot by the agreement covering it, so that a future-dated tariff change is reflected.
10. As an account holder, I want the Cost Forecast to price each remaining half-hour by the agreement covering it and my usual usage at that time of day, so that a Billing Period that spans a tariff change is projected accurately.
11. As an account holder whose Agile agreement has ended, I want Agile prices to keep being fetched, so that the Agile Prices panel still shows published prices.

## Implementation Decisions

- New Tariff Type `go`, detected from a tariff code containing `-GO-`. Defined in the domain glossary as **Octopus Go**.
- Go publishes two multi-hour product-rate windows per day (cheap 00:30–05:30 local time, peak otherwise). The existing generic rate fetch and storage already handle this, so no pricing-pipeline or schema change is needed.
- Cost Forecast: for `go`, every remaining half-hour of the Billing Period is priced individually (superseding the earlier effective-rate approach). The agreement covering the slot (by its `valid_from`/`valid_to`; in a gap between agreements or past the last one, the latest agreement that had begun by then, since a contract is expected to renew) supplies the product. The slot's unit rate is the published product rate covering it; beyond published rates, the rate at the same local clock time on the most recent fully published day. The slot's kWh is the average daily consumption times that time of day's share of usage, taken from real elapsed days wholly on the current tariff (a contract renewed onto the same product continues it, so its earlier days count; a switch part-way through a day excludes that day); with no such day the share is even across the day. A partly elapsed first slot counts for its remaining fraction. Other tariff types are unchanged.
- Rate lookups on the dashboard (Yesterday's Cost, Latest Consumption, Load Shift Efficiency, Daily Average Cost and Unit Price) additionally require the slot to fall before the chosen rate row's `valid_to`, so a missing rate drops the half-hour (and fails that day's completeness guard) instead of reusing an older rate.
- A day's standing charge, on the dashboard and in the elapsed-cost read (for every energy, not only Go), is the one in force at local midday, falling back to the largest when no half-hour is at midday. "Midday" is wall-clock 12:00 local time, which exists on every day including clock-change days. Estimated gap days keep their existing midday rule (twelve elapsed hours after local midnight), which differs only on the two clock-change days a year and only if a charge changes inside that hour.
- Cheapest Time Window: each future slot takes the agreement covering it (in a gap or past the last one, the latest that had begun), and the Agile forecast fills a slot only where that agreement's product is Agile.
- Pricing refresh: comparison rates are skipped only for products with an agreement that has not ended (or a zero-width one, which never ran), so a product from an ended electricity agreement (e.g. Agile after moving to Go) keeps its rates fetched, with that agreement's own tariff code and only from where it ended, so rates already stored for the agreement are not overwritten.
- Agile Prices panel (id 3): the actual series and the forecast de-duplication read the Agile product (the account's own if it is Agile, otherwise the newest Agile product in the catalogue, whose comparison rates are synced), not the account's current product.
- Cheapest Time Window panel (id 13): rates are expanded to half-hourly slots from whichever product-rate row covers each slot.
- New Stat panel "Current Tariff" (id 22), full width at the top of the electricity dashboard, showing the current electricity agreement's product display name, falling back to the tariff code. Existing panels shift down to make room. `stat` is added to the dashboard's required panel types.
- Dashboard reference doc (`electricity-queries.md`) is updated alongside the JSON.

## Testing Decisions

- Test external behaviour at existing seams: `CostForecastRetriever.refresh` against the SQLite-backed client with mocked Kraken HTTP, tariff-code to Tariff Type mapping, and the dashboard JSON tests under `scripts/tests`.
- Cost forecast prior art: `test_cost_forecast_retriever.py` (fixed and Agile tariffs). New tests in `test_cost_forecast_go.py` cover slot-by-slot Go pricing with skewed usage, the even-share fallback, previous-tariff and part-way-switch days, and a switch falling inside the remaining period. The pricing-refresh test in `test_pricing_ended_agreement.py` covers the ended-agreement case.
- Dashboard prior art: `scripts/tests/test_grafana_fuel_threshold_panels.py`. New tests assert the Current Tariff panel exists, reads the current agreement, `stat` is required, panels still do not overlap, and the Agile Prices and Cheapest Time Window queries are tariff-scoped as specified.
- The dashboard SQL cannot be executed locally (no MariaDB); this is stated in the PR and needs a manual check in Grafana.

## Out of Scope

- Go-specific panels (e.g. cheap-window share of usage).
- Forecasting Go rate changes beyond repeating the latest published day's pattern.
- The projection's standing charge, which stays the current agreement's for every remaining day.
- Slot-by-slot projection when the current agreement is not Go but a Go agreement starts later in the period; the remainder is projected as the current tariff projects today.
- Gas tariffs, the gas dashboard, and Economy 7 handling.
- Rewriting the non-compliant glossary format.

## Further Notes

- Go projection assumes the household's recent time-of-day usage pattern continues.
- The Cheapest Time Window on Go can only look as far ahead as Octopus has published rates.

# octopus-app: price each gap day with the agreement covering that day

## Problem Statement

The cost forecast prices consumption gap days (days with no or incomplete consumption rows) using the agreement that is current at `as_of`, for every gap day in the billing period. `read_elapsed_billing_period_costs` instead joins each instant to the agreement valid at that time. If a tariff renewal or switch falls inside the billing period, a gap day before the change is priced with the new product's rates, so it is priced at the wrong tariff, or, when the new product has no rates for that earlier day, the whole refresh raises even though the old product has them. The same applies to the earlier-full-day fallback for rate-less gap days. The bug is latent: it only bites when a renewal falls inside a billing period.

## Solution

Each gap day is priced from the agreement whose validity range covers that local day. The earlier-full-day fallback for a rate-less gap day uses the same agreement as the day it is estimating. A gap day that no agreement covers fails the refresh with a clear error, as a missing current agreement already does.

## User Stories

1. As the account holder, I want a gap day before a tariff renewal priced with the product that was in force that day, so that the billing-period cost is not mispriced.
2. As the account holder, I want a gap day after the renewal priced with the new product, so that the forecast reflects the tariff actually charged.
3. As the account holder, I want a rate hole on a pre-renewal gap day estimated from that same (old) product's earlier days, so that the refresh does not fail because the new product lacks rates for that period.
4. As the account holder, I want a gap day no agreement covers to fail the refresh with an error naming the day and energy, so that a forecast is never persisted with a silently unpriced day.
5. As the account holder, I want periods with a single agreement to behave exactly as before.

## Implementation Decisions

- A per-day agreement lookup returns the agreement whose `[valid_from, valid_to)` range contains the local day's midday (the instant the standing charge already reads), with `valid_to=None` unbounded; it raises a `RuntimeError` naming the day and energy when none covers it. It shares its range predicate with the current-agreement lookup.
- `_fill_zero_consumption_days` resolves the agreement for each gap day and passes it to `_price_gap_day`; it no longer takes a single agreement. The cap-exceeded error names the product of the day that tripped it.
- The earlier-full-day fallback keeps using the agreement it is given, which is now the gap day's. If the product changed at the renewal day itself and that day has a rate hole, the walk back looks for the new product's rates on days before it existed and may raise "no fully published day"; accepted and noted in ADR-0026.
- `_project_remaining_cost` still uses the current agreement; remaining days are priced at the current tariff.
- No schema, config or dashboard changes.

## Testing Decisions

- Seam: `CostForecastRetriever.refresh` with the existing real-client fixtures in `test_cost_forecast_gap_pricing.py` and `test_cost_forecast_estimated_rates.py`; assert on the persisted forecast and raised errors, not internals.
- Scenarios: gap day before a renewal priced with the old product; gap day after with the new product; an old-product rate hole estimated from the old product's earlier day; a gap day with no covering agreement raises naming the day; a single-agreement period is unchanged (existing tests stay green).
- Prior art: `test_cost_forecast_agreement_selection.py`, `test_cost_forecast_gap_pricing.py`, `test_cost_forecast_estimated_rates.py`.

## Out of Scope

- Pricing the projected remaining days across a future renewal.
- Choosing the earlier-full-day agreement by the earlier day's own validity.
- Changing how `read_elapsed_billing_period_costs` joins agreements.

## Further Notes

Found in the Copilot review of #596; pre-existing and not specific to the estimated-rates fallback added there.

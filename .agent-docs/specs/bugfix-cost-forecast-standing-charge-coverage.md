# Cost forecast: require rate coverage only where it is used for today's standing-charge-only day

## Problem Statement

The cost forecast has not updated since 2026-09-28. `cost_forecast_refresh` fails every day at 04:00 UTC (five attempts, then nothing until the next day) with `No product_rate found for AGILE-24-10-01 on <today> -- cannot compute actual_cost_to_date without silently omitting that day's standing charge or estimated variable cost.` The failing day is always the run's own date, and because the exception aborts the refresh before it persists, no forecast is written at all.

## Solution

For a day that is priced without an estimated variable cost, the forecast requires published rates only at the instant the standing charge is read (local midday), not across the whole local day. Days that do need an estimated variable cost keep the existing strict full-day requirement. A missing rate where one is genuinely needed still raises, with a clear message.

## User Stories

1. As the operator, I want the cost forecast to be written every day, so that dashboards show current figures.
2. As the operator, I want a day's cost computed only from the rates that day's calculation actually uses, so that an irrelevant unpublished hour cannot block the whole forecast.
3. As the operator, I want a genuinely missing rate to still fail loudly, so that I never get a silently wrong cost.
4. As the operator, I want every run to keep re-attempting all gap days in the billing period, so that a gap filled by later data is picked up the next day.

## Implementation Decisions

- **Cause.** At 04:00 UTC the run's own date has no consumption yet, so it is a gap day. By design (ADR-0023) it is priced standing-charge-only, with no estimated variable cost, to avoid double counting with the remaining-hours projection. `_price_gap_day` nevertheless demanded published rates across the whole local day. Agile rates are published each afternoon up to 23:00 UK time on the next day. In BST that is 22:00 UTC while the local day ends at 23:00 UTC, so at 04:00 UTC the last hour of today is unpublished and the check raised. It will happen daily until the clocks go back on 2026-10-25. The standing charge itself only reads the rate at local midday, so the missing hour was irrelevant to the number being computed.
- **Change.** In `_price_gap_day`: when the day has no estimated kWh (the standing-charge-only case), require only that a rate covers local midday; otherwise keep the full-day coverage requirement and its existing error text. If the midday rate is missing the day still raises, naming the day and the missing midday coverage.
- **Unchanged.** Overlap resolution and its tiebreak, DST handling, the estimate logic, the error text of the strict path, and the behaviour of re-evaluating every day of the billing period on each run.
- **ADR-0023.** A dated note records that coverage is required only where used and why (Agile publish horizon against BST local midnight).

## Testing Decisions

- Tests use the existing SQLite-backed seam and the gap-pricing helpers (prior art: `test_cost_forecast_gap_pricing.py`).
- Cases: reproduce the live failure (as_of 04:00 UTC on a BST day, today has no consumption, rates end an hour short of local midnight, so refresh persists a forecast whose today component is the standing charge only); a standing-charge-only day with no rate at local midday still raises; a gap day that needs a variable cost, with the same missing tail, still raises the strict error; a fully covered standing-charge-only day gives the same numbers as before.

## Out of Scope

- Changing the job schedule, adding retries, or pricing an unpublished tail with a substitute rate.
- The wider policy for a past gap day whose rates are genuinely missing (decided in principle: estimate the missing part, persist, and flag it visibly) and the move to an hourly run. Both are tracked in [#575](https://github.com/mholubinka1/home-monitoring/issues/575) and need a design session; this fix deliberately leaves the strict behaviour for those days untouched.
- Touching the live system or the forecast's stored data.
- The other gap-fill rules in ADR-0023.

## Further Notes

- Diagnosed from live data: the Agile rate table is continuous with no gaps or overlaps, and its latest window ends one hour short of the local day end, as above. The job re-evaluates every day of the billing period on every run, so once this check no longer blocks today, a forecast is written daily.

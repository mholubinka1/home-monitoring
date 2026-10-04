# Cost forecast estimates missing rates instead of failing

## Problem Statement

The cost forecast refresh is all-or-nothing. When a past gap day that needs a variable cost has missing or incomplete published rates, `_price_gap_day` raises, the whole refresh fails, and no forecast is written. The previous forecast goes stale and Grafana shows no current figure. The refresh also runs only once a day at 04:00 UTC, so a gap that fills an hour later is not reflected until the next day. Rates for each agreement are re-fetched hourly, so most holes self-heal quickly; the forecast should survive them meanwhile.

## Solution

When a past gap day has missing or incomplete rates, the forecast prices the uncovered part at an estimated rate, still writes the forecast, and flags it as partly estimated, visibly in the database and on the Grafana dashboard. Estimation is limited to a small number of past days; a longer hole still fails loudly because it signals a real problem. The refresh runs hourly, so the flag clears on its own once the rates arrive.

## User Stories

1. As the account holder, I want the cost forecast to be written even when a recent past day has incomplete published rates, so that the dashboard always shows a current figure.
2. As the account holder, I want a forecast that used estimated rates to be flagged, so that I know the figure is approximate and which days to distrust.
3. As the account holder, I want the flag to clear automatically once the missing rates are published, so that I do not have to act on a self-healing condition.
4. As the account holder, I want the forecast to still fail when more than 3 days are missing rates, so that a wrong product code or an upstream outage is not hidden behind a confident estimate.
5. As the account holder, I want today to stay standing-charge-only as it is now, so that the rest of today's cost is counted exactly once.
6. As the account holder, I want the forecast refreshed every hour, so that a gap fills within an hour of its rates arriving.
7. As the account holder, I want the Grafana dashboard to show when the forecast is estimated and for which days, so that I can read the figure with the right level of trust.
8. As the maintainer, I want the policy recorded in an ADR and the glossary, so that the next reader knows why the forecast estimates rather than raises.

## Implementation Decisions

- **Which days qualify.** Only past gap days whose rates are missing or incomplete (any part of the local day not covered by published rates) and which need a variable cost. Today (standing-charge-only) is unchanged from ADR-0023's 2026-10-03 note.
- **Cap.** At most 3 days per energy per refresh have their rates estimated. A 4th raises the existing error. The cap counts rate-estimated days only, not all gap-filled days.
- **Estimated unit rate.** The uncovered stretches of the day are priced at the time-weighted average unit rate of that same day's published segments (each segment weighted by its duration). If the day has no published segments at all, the unit rate is the time-weighted average of the nearest earlier fully published day, searched back at most 7 days. If none exists in that window, raise as before.
- **Estimated standing charge.** If the day has a rate covering local midday, use it as now. Otherwise use the standing charge from the same nearest earlier fully published day used for the unit rate.
- **Flag meaning.** `rates_estimated` means a past gap day was priced with an estimated rate. kWh-estimated gap days with fully published rates remain unflagged, as in ADR-0023.
- **Model.** `DailyCostSummary` gains `rates_estimated: bool = False`. `CostForecast` gains `rates_estimated: bool` and `estimated_days` (the estimated dates, comma-separated ISO, or none).
- **Schema.** `cost_forecast` gains `rates_estimated` (boolean, not null, default false) and `estimated_days` (nullable text), added through the existing additive schema sync. Existing rows read as not estimated.
- **Persistence.** `write_cost_forecast` writes both columns. Each refresh appends a new `cost_forecast` row (history is kept), so the flag clears when the newest row for the energy and billing period is unflagged. Anything reading the flag, including the dashboard, must read the latest row.
- **Cadence.** `cost_forecast_refresh` is scheduled every hour, like `pricing_refresh` and `consumption_refresh`. The five-attempt backoff is unchanged. No new alert.
- **Dashboard.** The Grafana dashboard JSON shows the flag and the estimated days alongside the cost forecast. The current JSON is to be supplied by the user from their live Grafana instance before this slice; it is compared with the repo copy first.
- **Records.** New ADR-0026 records the policy; a pointer note in ADR-0023 replaces its "a day not fully covered by known rates still raises" statement; `context.md` gains the terms for estimated rates and the flag.

## Testing Decisions

- Test external behaviour at the existing seams, not internals.
- **Retriever refresh seam** (`CostForecastRetriever.refresh`, as in the gap pricing tests): a BST horizon where the final Agile slots before local midnight are missing; a past day with no published rates falling back to the earlier full day; incomplete-day pricing against hand-computed figures; the cap raising on the 4th day; today unchanged; gas as well as electricity.
- **Persistence and schema-sync seams**: the flag round trip through MariaDB; the additive sync adding both columns to an existing table and leaving old rows unflagged.
- **Scheduling seam**: the cost forecast job registers hourly.
- Prior art: `test_cost_forecast_gap_pricing.py`, `test_cost_forecast_persistence.py`, `test_schema_sync.py`, `test_refresh_scheduling.py`.

## Out of Scope

- A new alert or notification for a persistent upstream hole.
- Flagging kWh-estimated gap days.
- Using `agile_forecast` predictions for past days.
- #585 (reserved for another session) and other dashboard issues. #584 was folded in at the user's request because it broke every panel and touches the same file.

## Further Notes

- Issue #575 holds the original open questions; this spec answers them: fallback source, flag storage, which days qualify, cadence and failure behaviour, records, tests.
- The 2026-10-03 note in ADR-0023 (#574) stays in force.

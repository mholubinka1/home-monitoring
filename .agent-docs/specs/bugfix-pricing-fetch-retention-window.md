# Pricing fetches only the retention window

## Problem Statement

Every hour the pricing refresh downloads the full rate history of the account's own tariffs (from each agreement's start) and of every comparison product (back to 2024-01-01), about 2.5 minutes of Octopus API calls, and upserts about 42,000 `product_rate` rows. Since the summary and pruning became daily ([#660](https://github.com/mholubinka1/home-monitoring/pull/660)), the 04:00 prune deletes the roughly 40,000 rows whose rates ended more than 45 days ago, and the next pricing run downloads and writes them all again, logging SQLAlchemy duplicate-identity warnings as it does. On 2026-10-10 the table held 41,771 rows, 39,585 of them already prunable. Nothing reads those old rates: the cost forecast prices the current billing period and every dashboard rate query joins raw consumption, which is itself kept for 45 days. The work is wasted and the warnings are noise.

## Solution

The pricing refresh asks Octopus only for rates within the Retention Window (the same 45 days the pruner keeps). A rate already in force at the start of the window is still returned (the API returns rates overlapping the requested period; checked live on 2026-10-10: a request from 2026-10-05 returned the rate valid from 2026-09-30 with no end), so current fixed and variable rates are unaffected. Each run then handles about 45 days of rows, the prune removes only rows that have genuinely aged out, and nothing re-inserts them.

## User Stories

1. As an operator, I want the hourly pricing refresh to download only the rates the app can use, so that the Pi does not spend minutes an hour re-fetching years of history.
2. As an operator, I want the daily prune to remove rows that stay removed, so that `product_rate` holds about 45 days of rates and the logs carry no duplicate warnings.
3. As the account holder, I want cost figures and dashboards unchanged, so that the fix costs me nothing.
4. As a maintainer, I want "how far back pricing fetches" and "how long rates are kept" to come from one setting, so that they cannot drift apart.

## Implementation Decisions

- **One setting.** The pricing retriever takes the existing `retention` refresh setting (`retention_days`, 45), the one `DataPruner` uses, rather than a new constant.
- **Window start.** Window start is the refresh time minus the retention days. The refresh time is injectable (`as_of`, defaulting to now), as in the other jobs.
- **Own agreements.** Rates are requested from the later of the agreement's start and the window start, to the agreement's end (open-ended stays open). An agreement whose end is before the window start is not fetched at all. The existing zero-or-negative-width skip is unchanged.
- **Comparison products.** Rates are requested from the window start, open-ended, instead of with no bounds.
- **No other changes.** The API client, the `product_rate` table and the write path are unchanged; rows already stored that are older than the window are removed by the next daily prune. No migration.
- **Docs.** The glossary's Retention Window entry says the pricing refresh fetches only the window. No ADR: the retention choice is already ADR-0003, and this is easy to reverse.

## Testing Decisions

- One seam: `PricingRetriever.refresh()` with the real Octopus API client against mocked HTTP endpoints and the SQLite fixture (`apps/octopus-app/tests/test_pricing_retrieval.py`). Tests assert the `period_from` / `period_to` each rate request sends, which is the external behaviour at the API boundary, and what ends up stored.
- Good tests: an own agreement that started long ago is requested from the window start; one that started inside the window is requested from its own start; one that ended before the window is not requested; a comparison product is requested from the window start; a rate in force from before the window that the API returns is stored; the existing pricing tests still pass.
- Prior art: the existing request-mocking helpers in that file and the injectable `as_of` used by `ConsumptionSummaryRetriever.refresh` and `DataPruner.run`.

## Out of Scope

- Changing the 45-day retention, the prune itself, or the hourly pricing cadence.
- The `SAWarning` itself (a product's rate listing that names the same row twice in one write); it is expected to stop once old rows are no longer re-inserted after each prune, and is only revisited if it does not.
- Rate history for analysis beyond 45 days (nothing needs it today; widening the window later is a setting change).

## Further Notes

- Measured on the Pi on 2026-10-10: a pricing run spans about 2.5 minutes; the 04:00 prune deleted 39,573 `product_rate` rows; the table then held 41,771 rows with 39,585 prunable again.

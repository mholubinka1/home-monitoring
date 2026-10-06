# octopus-app: fewer consumption requests and a local-day `as_of` in the summary refresh

## Problem Statement

Two small robustness gaps remain after #606 and #608.

1. Consumption is fetched 100 rows per page. A routine 45-day window is about 2,100 rows per meter, so each refresh or daily backfill makes 22 requests per meter (about 44 a day for the daily backfill, and the same again on every container restart). A failed page restarts the whole window from page 1, so every extra request is extra exposure to timeouts and upstream paging problems. Octopus documents page sizes up to 25,000.
2. `ConsumptionSummaryRetriever.refresh` takes "today" from `datetime.now(UTC).date()`, but the window it feeds is made of London local days. Between 00:00 and 01:00 BST the UTC date is a day behind, so the window starts a day early. This has no data impact (one extra day is re-summarised, never one fewer, and the weekly job does not run in that hour), but it breaks the rule that UTC is for API boundaries and local time defines a day (ADR-0010).

## Solution

Fetch consumption 5,000 rows per page, so every routine window is a single request. Take the summary retriever's `as_of` from the local date and let callers pass it in, so the boundary is testable and consistent with the other retrievers.

## User Stories

1. As the operator, I want a routine consumption fetch to be one request per meter, so that there are far fewer chances for a timeout or paging problem and the daily backfill takes about a second instead of half a minute.
2. As the operator, I want the summary refresh to define "today" as the dashboards do, so that its window boundary is always right.
3. As a future reader, I want day arithmetic to use the local date everywhere, so that nobody reuses a UTC date for a day boundary.

## Implementation Decisions

- `ConsumptionClient`'s `DEFAULT_PAGE_SIZE` goes from 100 to 5,000 for both electricity and gas. Descending order, request parameters and `next` handling are unchanged. The choice of 5,000 over the documented 25,000 maximum, and the measurements behind it, are recorded in ADR-0027 (page size section).
- `ConsumptionSummaryRetriever.refresh` gains an optional `as_of: datetime | None = None` (matching `CostForecastRetriever.refresh`) and derives its date with `local_day.to_local_date(as_of or datetime.now(UTC))`. Scheduler and startup wiring are unchanged. This is delivered as its own commit.
- No schema, config, schedule or retry changes.

## Testing Decisions

- Page size: update the tests that pin `page_size=100`: the endpoint-building test (which becomes the regression test, for both electricity and gas) and the mocked URLs in the retrieval and summary-backfill tests.
- `as_of`: through `ConsumptionSummaryRetriever.refresh(as_of=...)` with seeded raw rows, at 23:30 UTC on a BST day (00:30 local the next day). The window must be exactly 14 local days: the day just outside it (an existing, stale summary row) stays untouched and the first day inside it is re-summarised. It fails on the UTC date, which starts the window a day early and rewrites the outside day.
- Prior art: `test_consumption_endpoint_building.py`, `test_consumption_retrieval.py`, `test_consumption_summary_backfill.py`, and the existing summary-window tests.

## Out of Scope

- Sending `period_to` (tested in #605: it does not stop ascending-paging duplication; if windows are ever chunked, `interval_start <= period_to` is inclusive).
- Dedupe or a rows-versus-`count` guard (decided against in #605).
- Rate and product endpoints, schedules, retry behaviour, and reporting the paging behaviour to Octopus.

## Further Notes

Live measurements (Pi, 2026-10-06, read-only) are in ADR-0027. Sources: Guy Lipman's Octopus API guide and the official endpoints guide on page size, ordering and time zones.

# octopus-app: request consumption newest-first so paging returns every interval once

## Problem Statement

The yearly-comparison panels read `daily_consumption_summary`, whose 2-year history was written by a one-time backfill on 2026-07-22. That backfill fetches consumption with `order_by=period` (oldest first) and `page_size=100`; against the live Octopus API that combination returns overlapping and skipping pages. A full 2-year fetch on 2026-10-05 returned 34,946 electricity rows with only 22,296 distinct interval starts, and 33,654 gas rows with 21,620 distinct. The backfill sums every returned row, so each day's total in the summary is randomly over- or under-counted, for both energies. Raw `consumption` and the cost forecast are unaffected. While investigating 26 apparently missing gas summary days, about 22 of them turned out to be genuine gaps in Octopus's own data.

## Solution

Consumption is requested with `order_by=-period` (newest first), which returns every interval exactly once with the row count equal to the API's own `count`. The existing summary history is then repaired by re-running the one-time backfill once by hand after deploy. No other behaviour changes.

## User Stories

1. As the household's energy analyst, I want the yearly-comparison panels to rest on correct daily totals, so that year-on-year changes are meaningful.
2. As the operator, I want every paged consumption fetch (startup, daily backfill, hourly refresh, summary backfill) to return each interval exactly once, so that no data is silently skipped or double counted.
3. As the operator, I want a regression test that pins the request ordering, so that a future change cannot reintroduce ascending paging unnoticed.
4. As the operator, I want a documented, verified one-time repair of the existing summary history, so that I know when the panels are trustworthy again.
5. As a future reader, I want the genuine upstream gas gaps documented as expected, so that nobody chases them as a bug.

## Implementation Decisions

- `ConsumptionClient`'s request parameters change `order_by` from `period` to `-period`; page size and everything else are unchanged. Both the electricity and gas fetch paths share the one parameter builder.
- Nothing downstream depends on order: the retriever advances its cursor with `max(c.end)`, raw rows are upserted keyed by interval start, and the summary backfill only sums.
- Rate and product endpoints are not changed: their multi-page results were verified complete.
- No defensive dedupe or row-count guard is added (decided in the design session); recorded as considered options in ADR-0027.
- The repair is an operational step, not code: after deploy, `ConsumptionSummaryBackfill.run()` is executed once inside the `octopus-app` container; it upserts each fetched (energy, date), overwriting wrong totals, without touching `job_run` or raw tables.
- Docs: ADR-0027 and a note on the Consumption Summary glossary entry (both written during design).

## Testing Decisions

- Seam: the real consumption fetch path through `responses`-mocked HTTP, as the existing consumption endpoint-building, retrieval and summary-backfill tests already do; assertions are on the request the app sends and the data it stores.
- The tests that assert `order_by=period` in query strings or mocked URLs are updated to `-period`; the endpoint-building test that names the parameter is renamed to say newest-first and acts as the regression test.
- The summary backfill test continues to prove two years of fetched consumption are summarised without writing raw rows.
- Prior art: `test_consumption_endpoint_building.py`, `test_consumption_retrieval.py`, `test_consumption_summary_backfill.py`.

## Out of Scope

- Changing the page size, adding dedupe, or a rows-versus-`count` integrity check.
- Rate, product, account or pricing endpoints.
- Estimating or filling the genuine upstream gas gaps.
- A versioned job name or any automatic repair mechanism.

## Further Notes

Evidence and reproduction (live Pi, 2026-10-05): ascending paging duplicated 12,650 of 34,946 electricity rows and 12,034 of 33,654 gas rows over the 2-year window; descending produced zero duplicates and `rows == count` for both. The repair is verified after the manual run by comparing each summary day against a fresh descending fetch.

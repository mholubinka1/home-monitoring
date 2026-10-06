# Consumption is requested newest-first, because ascending paging duplicates and skips intervals

`ConsumptionClient` asked Octopus for consumption with `order_by=period` (oldest first) and `page_size=100`, then followed the `next` links. Against the live API that combination returns pages that overlap and skip: a full 2-year fetch on 2026-10-05 returned 34,946 electricity rows of which only 22,296 had distinct `interval_start` values (12,650 duplicates), and 33,654 gas rows of which only 21,620 were distinct (12,034 duplicates). The same fetch with `order_by=-period` returned every interval exactly once, with the row count equal to the API's own `count` for both energies, and a single `page_size=25000` request was also exact. A 4-day window (193 rows) already showed it: 51 of 193 electricity rows duplicated in ascending mode, none in descending.

Consumption is therefore requested with `order_by=-period`. Nothing downstream depends on the order: `ConsumptionRetriever` advances its cursor with `max(c.end)`, raw rows are upserted by interval start, and the summary backfill only sums. This is surprising without context (ascending is the natural choice for a forward cursor), so it is recorded here; the alternative of a single very large page was rejected because a 2-year gas fetch (about 33,000 rows) exceeds the 25,000-row maximum anyway.

## Impact

- **Raw `consumption` and cost figures were unaffected.** Raw rows are small-window upserts keyed by interval start and are re-fetched daily, and the table is complete (48 rows per day). The cost forecast reads raw rows.
- **The stored `daily_consumption_summary` was checked and is correct; no repair was needed.** The ascending-paging defect could corrupt any large paged sum (the summary backfill sums every returned row), and was first suspected of having done so to the one-time `yearly_comparison_backfill` (2026-07-22). On 2026-10-06 an exact, zero-duplicate descending fetch, bucketed by local day, was compared with the stored table: all 1,408 comparable days (715 electricity, 693 gas) matched to three decimal places, so the history is intact. Not compared: the trailing 14 days (re-summarised weekly from raw), the partial first day, and days before about 2024-10-06, which the API no longer serves. What this does not establish is why that fetch was unaffected: the defect reproduces today, but whether it was present on 2026-07-22 was not measured. The ordering fix prevents it hurting any later large fetch either way.
- **Rates and products are not affected.** The multi-page unit-rate endpoints (Agile, 25 to 30 pages) have `count == rows == distinct` with the app's default parameters.

## The backfill buckets by local day

The same check showed the stored table follows **local-day** bucketing (it matches local-day totals on every day compared, and UTC-date totals on none of the 358 summer electricity days), as does the weekly summary job. `ConsumptionSummaryBackfill` was written to key intervals by `point.start.date()` while `start` still carried Octopus's local offset; normalising timestamps to UTC on ingest (`3a97b19`, 2026-07-28) silently turned that into UTC-date bucketing, so a re-run would have shifted BST days. It now keys by `local_day.to_local_date(point.start)`, pinned by a BST-boundary test.

## Genuine upstream gaps

Some gas summary days are missing because Octopus returns no gas readings for them (about 22 days since 2024-10-06, in runs of 1 to 3 days), not because of this defect, and they are left as they are. The API no longer serves anything before about 2024-10-06, so earlier days kept by the 2026-07-22 backfill cannot be re-verified and keep their existing values.

## Page size is 5,000

The page size was also raised from 100 to 5,000 (Octopus documents up to 25,000), so a routine 45-day window of about 2,100 rows per meter is one request instead of 22. Measured on the Pi on 2026-10-06 with newest-first ordering, in all 16 combinations of energy, window (45 days, 2 years) and page size (100, 1,000, 5,000, 25,000) the rows, distinct interval starts and the API's `count` agreed, and `next` links kept `page_size` and `order_by`:

| Window | Page 100 | Page 5,000 | Page 25,000 |
| --- | --- | --- | --- |
| 45 days (electricity / gas) | 22 requests, 17.0 s / 12.7 s | 1 request, 0.6 s / 0.6 s | 1 request, 1.0 s / 0.5 s |
| 2 years (electricity / gas) | 349 / 337 requests, 332 s / 298 s | 7 requests, 9.0 s / 7.3 s | 2 requests, 2.7 s / 2.7 s |

Bytes transferred are identical at every size. Peak Python heap per page was 0.7 MB at 1,000, 2.9 MB at 5,000 and 14 MB at 25,000 (process RSS 42 MB to 69 MB at 25,000, against about 2 GB available on the Pi); the slowest single request at 25,000 rows took 1.4 s against the 30 s transport timeout. A failed page restarts the whole window, so the request count is the exposure: routine runs go from about 44 requests a day for the daily backfill (plus the same on each restart) to 2.

5,000 rather than the documented maximum because every routine window already fits in one page, 25,000 only shortens the one-off 2-year backfill (done) at about 5 times the memory, and 5,000 leaves a 5 times margin below Octopus's maximum.

## Considered options

- **Dedupe by interval start in the backfill:** rejected, it hides duplicates but cannot recover the skipped intervals, so totals would still be wrong.
- **Fail the backfill when rows differ from the API's `count`:** rejected for now, it needs the fetch interface to carry the count for a defect that is verified gone; the per-day row counts used to find this make a recurrence easy to spot.

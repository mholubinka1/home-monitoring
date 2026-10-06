# octopus-app: the summary backfill buckets by local day, and ADR-0027 stops claiming a corruption that did not happen

## Problem Statement

Two loose ends from #605.

First, `ConsumptionSummaryBackfill` was written on 2026-07-21 to bucket intervals with `point.start.date()` when `start` still carried Octopus's local offset, so it bucketed by local day, which is what the weekly summary job and the dashboards use. Commit `3a97b19` (2026-07-28) normalised consumption timestamps to UTC on ingest, which silently turned the same expression into UTC-date bucketing. The stored `daily_consumption_summary` is unaffected (the backfill already ran), but a re-run would shift every BST day by up to an hour into the previous day and disagree with the weekly job, whose own comment still says the backfill buckets by local day.

Second, ADR-0027 (merged in #606) says the 2026-07-22 backfill wrote wrong history and prescribes a one-time manual repair. On 2026-10-06 the stored table was compared with an exact, zero-duplicate fetch bucketed by local day and all 1,408 comparable days matched to three decimal places, so that claim was an unchecked inference and the repair is unnecessary.

## Solution

The backfill buckets each interval by its Europe/London local date, matching the stored table and the weekly job. ADR-0027 is corrected: the paging defect and the descending-order fix stand, the claimed corruption and the repair step are removed, and the verification is recorded.

## User Stories

1. As the operator, I want a re-run of the summary backfill to produce the same daily totals as the weekly job, so that the yearly-comparison panels do not shift days at BST boundaries.
2. As a future reader, I want ADR-0027 to state only what was verified, so that nobody plans a repair or doubts the stored history on the strength of an unchecked claim.

## Implementation Decisions

- `ConsumptionSummaryBackfill.run` keys each interval by `local_day.to_local_date(point.start)` instead of the UTC date of `point.start`. No other behaviour changes: still summing, still no raw writes, same fetch window.
- The existing comment in `read_consumption_summarization_window` that the backfill buckets by local day becomes true again; no change needed there.
- ADR-0027: remove the "wrote wrong history" claim and the one-time repair, add the 2026-10-06 verification (1,408 days matched, partial and trailing days excluded), and add that the backfill's day bucketing is local and why it needed restoring. Keep the paging evidence, the descending decision, the rates finding and the genuine upstream gaps. The Consumption Summary glossary note is already neutral and stays.
- No schema, config or dashboard changes; no deploy action beyond the normal Watchtower cycle.

## Testing Decisions

- Seam: `ConsumptionSummaryBackfill.run` with the existing real-client source and `responses`-mocked consumption endpoint in `test_consumption_summary_backfill.py`, asserting on stored `daily_consumption_summary` rows.
- New scenario: on a BST day, intervals starting at 00:00 and 00:30 local (23:00 and 23:30 UTC the previous day) and 23:30 local all count towards that local day, and no row is stored for the previous UTC date. It fails on UTC bucketing.
- Existing scenarios keep passing (their timestamps are UTC and mid-day, so both bucketings agree).

## Out of Scope

- Re-running or repairing `daily_consumption_summary` (verified correct).
- Changing the weekly summary job, raw ingestion, or the paging fix.
- A dedupe or rows-versus-count guard (decided against in the design session for #605).

## Further Notes

Evidence is in the #605 comments: stored days match local-day totals on all 715 electricity and 693 gas days compared, and match UTC-date totals on none of the 358 summer electricity days.

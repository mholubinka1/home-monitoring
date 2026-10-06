# Issues: bugfix-summary-backfill-local-day

## octopus-app: summary backfill local-day bucketing, and correct ADR-0027 — [#605](https://github.com/mholubinka1/home-monitoring/issues/605)

**Blocked by**: None

**User stories**: 1, 2

### What to build

Make `ConsumptionSummaryBackfill` bucket intervals by local date again (commit `3a97b19` had turned it into UTC-date bucketing), with a BST-boundary test through `ConsumptionSummaryBackfill.run`. Correct ADR-0027: remove the unchecked claim that the 2026-07-22 backfill wrote wrong history and the one-time repair, record the 2026-10-06 verification that the stored table matches exact local-day totals, and note that the backfill's bucketing is local.

### Acceptance criteria

- [ ] Intervals at 00:00 and 00:30 local and 23:30 local on a BST day all count towards that local day, with no row stored for the previous UTC date (test fails on UTC bucketing)
- [ ] Existing summary backfill tests still pass
- [ ] ADR-0027 no longer claims corrupted history or prescribes a repair, and records the verification and the local bucketing
- [ ] The earlier acceptance criterion about re-running the backfill after deploy is withdrawn: the stored table was verified correct on 2026-10-06 (see the correction comment on #605)

---

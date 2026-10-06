# Issues: bugfix-consumption-pagination-order

## octopus-app: consumption ascending paging duplicates/skips intervals, corrupting daily_consumption_summary — [#605](https://github.com/mholubinka1/home-monitoring/issues/605)

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5

> **Since corrected (2026-10-06).** The stored `daily_consumption_summary` was verified correct, so the repair described below was never needed or run; see ADR-0027 and the correction comment on #605. The repair criterion at the end is withdrawn.

### What to build

Request consumption newest-first (`order_by=-period`) so paged fetches return each interval exactly once, update the tests that assert `order_by=period`, and record the decision in ADR-0027. Repair the existing summary history by re-running the one-time summary backfill once by hand after deploy, then verify it against a fresh descending fetch.

### Acceptance criteria

- [ ] Consumption requests (electricity and gas) use `order_by=-period`
- [ ] A regression test pins the request ordering; existing tests asserting `order_by=period` are updated
- [ ] ADR-0027 and the Consumption Summary glossary note are in place
- [x] ~~After deploy, the one-time backfill is re-run by hand and `daily_consumption_summary` is verified against a fresh descending fetch for both energies~~ Withdrawn: instead verified without writing, all 1,408 comparable days already matched (ADR-0027)

---

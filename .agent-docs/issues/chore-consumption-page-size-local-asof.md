# Issues: chore-consumption-page-size-local-asof

## octopus-app: fewer consumption requests (page size 5000) and a local-day as_of in the summary refresh — [#609](https://github.com/mholubinka1/home-monitoring/issues/609)

> Work complete — [PR #610](https://github.com/mholubinka1/home-monitoring/pull/610) ready to merge.

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

Raise the consumption page size from 100 to 5,000 so every routine window is one request (measurements in ADR-0027), and give `ConsumptionSummaryRetriever.refresh` an optional `as_of` whose local date drives its window. Two separate commits.

### Acceptance criteria

- [x] Consumption requests (electricity and gas) use `page_size=5000`; the tests pinning 100 are updated and the endpoint-building test is the regression test
- [x] `ConsumptionSummaryRetriever.refresh(as_of=...)` derives its window from the local date, with a test at 23:30 UTC on a BST day that fails on the UTC date
- [x] ADR-0027 records the page size and the measurements
- [x] The two changes are separate commits

---

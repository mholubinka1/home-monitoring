# Issues: bugfix-gap-day-agreement-per-day

## octopus-app: gap days are priced with the agreement current at as_of, not the one covering the day — [#597](https://github.com/mholubinka1/home-monitoring/issues/597)

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5

### What to build

Price each consumption gap day with the agreement whose validity range covers that local day (local midday, the standing-charge convention) rather than the agreement current at `as_of`; use the same agreement for the earlier-full-day fallback; raise a clear `RuntimeError` naming the day and energy when no agreement covers a gap day. Add an ADR-0026 note.

### Acceptance criteria

- [ ] A gap day is priced from the agreement whose validity range covers that local day, not the one current at `as_of`.
- [ ] The earlier-full-day fallback for a rate-less gap day uses the same agreement as the day it is estimating.
- [ ] A test covers a renewal boundary inside the billing period (gap day before the renewal, product changes at the boundary).
- [ ] A gap day that no agreement covers fails the refresh with an error naming the day and energy.
- [ ] Periods with a single agreement behave as before (existing tests green).

---

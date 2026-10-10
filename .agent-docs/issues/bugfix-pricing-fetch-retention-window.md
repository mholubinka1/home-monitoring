# Issues: bugfix-pricing-fetch-retention-window

## PRF-1 · Pricing fetches only the retention window — [#661](https://github.com/mholubinka1/home-monitoring/issues/661)

**Blocked by**: None

**User stories**: 1, 2, 3, 4

### What to build

The hourly pricing refresh asks Octopus only for rates within the 45-day Retention Window, using the same `retention` setting as the pruner. Own agreements are requested from the later of their start and the window start; comparison products from the window start. A rate in force from before the window is still stored, so rows the daily prune removes stay removed and the duplicate warnings stop.

### Acceptance criteria

- [ ] Given an own agreement that began years ago and is still open, when pricing refreshes, then its rates are requested from the window start, open-ended.
- [ ] Given an own agreement that began inside the window, then its rates are requested from the agreement's own start.
- [ ] Given an own agreement that ended before the window, then no rates are requested for it.
- [ ] Given a comparison product, then its rates are requested from the window start, open-ended.
- [ ] Given the API returns a rate in force since before the window, then that rate is stored.
- [ ] The existing pricing tests still pass, and the glossary's Retention Window entry says the pricing refresh fetches only the window.

---

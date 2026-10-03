# Issues: bugfix-cost-forecast-standing-charge-coverage

> Work complete — [PR #576](https://github.com/mholubinka1/home-monitoring/pull/576) ready to merge.
> After deploy, confirm `cost_forecast_refresh` succeeds in `job_run` on the
> live database. The wider missing-rate policy is tracked in #575.

## octopus-app: cost forecast fails daily when today's last Agile hour is unpublished (BST) — [#574](https://github.com/mholubinka1/home-monitoring/issues/574)

**Blocked by**: None

**User stories**: 1, 2, 3, 4

### What to build

Fix the cost forecast, which has failed daily since 2026-09-29 with
`No product_rate found for AGILE-24-10-01 on <today>`. At 04:00 UTC the run's
own date is a gap day priced standing-charge-only (no estimated variable
cost), yet `_price_gap_day` demands published rates across the whole local
day. In BST the Agile publish horizon (23:00 UK local the next day = 22:00
UTC) is one hour short of local midnight (23:00 UTC), so the strict check
raises and no forecast is written. For a gap day with no estimated kWh,
require rate coverage only at local midday (where the standing charge is
read); keep the full-day requirement and error for days that need a variable
cost; still raise if the midday rate is missing. Add a dated note to ADR-0023.
The wider missing-rate policy and hourly cadence are tracked separately in
[#575](https://github.com/mholubinka1/home-monitoring/issues/575).

### Acceptance criteria

- [x] Given as_of at 04:00 UTC on a BST day with today a no-consumption gap
      day and rates ending an hour short of local midnight, refresh persists a
      forecast whose today component is the standing charge only.
- [x] Given a standing-charge-only day with no rate at local midday, refresh
      still raises.
- [x] Given a gap day that needs a variable cost with the same missing tail,
      the strict full-day error is still raised.
- [x] Given a fully covered standing-charge-only day, the numbers are
      unchanged from before.
- [x] ADR-0023 has a dated note about where coverage is required and why.

---

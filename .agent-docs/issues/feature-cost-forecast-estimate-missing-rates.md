# Issues: feature-cost-forecast-estimate-missing-rates

Parent: [#575](https://github.com/mholubinka1/home-monitoring/issues/575)

## octopus-app: estimate and flag incomplete rates on a past cost-forecast gap day — [#591](https://github.com/mholubinka1/home-monitoring/issues/591)

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5

### What to build

When a past gap day that needs a variable cost has missing or incomplete published rates, price the uncovered stretches at that same day's time-weighted average unit rate, still write the forecast, and flag it. End to end: `DailyCostSummary.rates_estimated`, `CostForecast.rates_estimated` and `estimated_days`, the additive `cost_forecast` columns via schema sync, and persistence. At most 3 days per refresh are rate-estimated; a 4th raises. Today (standing-charge-only) is unchanged. A day with no published rates at all is handled by the next slice and still raises here.

### Acceptance criteria

- [ ] Given a BST day whose last Agile slots before local midnight are unpublished, refresh writes the forecast with `rates_estimated` true and that date in `estimated_days`, priced at the same-day time-weighted average unit rate (hand-computed figure).
- [ ] Given 4 past days missing rates, refresh raises and writes nothing.
- [ ] Given fully published rates, `rates_estimated` is false and `estimated_days` is null.
- [ ] Given the rates arrive before a later refresh, the flag clears.
- [ ] Gas behaves the same as electricity.
- [ ] The additive schema sync adds both columns to an existing table; existing rows read as not estimated.
- [ ] kWh-estimated gap days with fully published rates remain unflagged.

---

## octopus-app: price a past gap day with no published rates from the earlier full day — [#593](https://github.com/mholubinka1/home-monitoring/issues/593)

**Blocked by**: #591

**User stories**: 2, 4

### What to build

A past gap day with no published rates at all is priced at the time-weighted average unit rate and the standing charge of the nearest earlier fully published day, counts toward the cap of 3, and is flagged like any other rate-estimated day. If no earlier fully published day exists, refresh raises as before.

### Acceptance criteria

- [ ] Given a past day with no rates and a fully published earlier day, the day is priced at that earlier day's time-weighted average unit rate and its standing charge, and flagged.
- [ ] Given no earlier fully published day exists, refresh raises.
- [ ] A day with no midday rate takes the earlier day's standing charge.
- [ ] Such days count toward the cap of 3.

---

## octopus-app: run cost_forecast_refresh hourly — [#592](https://github.com/mholubinka1/home-monitoring/issues/592)

**Blocked by**: None

**User stories**: 6

### What to build

Schedule `cost_forecast_refresh` every hour, like `pricing_refresh` and `consumption_refresh`, so a gap fills within an hour of its rates arriving. The five-attempt backoff is unchanged and no new alert is added.

### Acceptance criteria

- [ ] The scheduling test shows the cost forecast job registered to run every hour.
- [ ] The existing backoff and job-run recording behave as before.

---

## grafana: show the cost-forecast estimated-rates flag and days — [#594](https://github.com/mholubinka1/home-monitoring/issues/594)

**Blocked by**: #591 (needs the live dashboard JSON from the user)

**User stories**: 7

### What to build

Show the `rates_estimated` flag and `estimated_days` alongside the cost forecast on the Grafana dashboard. Start by getting the current dashboard JSON from the live Grafana instance and comparing it with the repo copy.

### Acceptance criteria

- [ ] The live dashboard JSON was compared with the repo copy before editing.
- [ ] The dashboard visibly indicates when the forecast is estimated and for which days, and shows nothing extra when it is not.
- [ ] Dashboard JSON validates and the existing panels are unchanged.

---

## docs: ADR-0026 and glossary for estimated cost-forecast rates — [#595](https://github.com/mholubinka1/home-monitoring/issues/595)

**Blocked by**: #591, #593

**User stories**: 8

### What to build

Add ADR-0026 recording the policy (estimate and flag, fallback chain, cap of 3, the two columns, hourly cadence). Add a pointer note to ADR-0023 replacing its statement that a day not fully covered by known rates still raises. Add the new terms (estimated rates, the flag) to `context.md`.

### Acceptance criteria

- [ ] ADR-0026 exists in the repo's ADR format.
- [ ] ADR-0023 points to ADR-0026 and the 2026-10-03 note is left in place.
- [ ] `context.md` defines the new terms.

---

## Folded in: grafana targets still carry "dataset": "octopus" — [#584](https://github.com/mholubinka1/home-monitoring/issues/584)

**Blocked by**: None

Folded into the #594 change at the user's request: the old `octopus` database was dropped, so every panel on the live dashboard returned a 500. All 16 `"dataset"` fields in `dashboard.json` are now `home_monitoring`.

### Acceptance criteria

- [ ] No `"dataset": "octopus"` remains in `data/grafana/dashboard.json`.
- [ ] After import, the dashboard panels load without the 500.

---

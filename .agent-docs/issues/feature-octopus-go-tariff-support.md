# Issues: feature/octopus-go-tariff-support

> Work complete — PR ready to merge.

## Cost Forecast prices Octopus Go at the effective rate paid

**GitHub issue**: #723

**Blocked by**: None

**User stories**: 1, 2

### What to build

Recognise a tariff code containing `-GO-` as the `go` Tariff Type and, for it, project the remaining Billing Period at the effective unit rate paid per kWh on real elapsed days, falling back to the time-weighted average of today's published windows when no real day exists. Add the **Octopus Go** glossary term. Keep the projection method within the repo's pylint limits.

### Acceptance criteria

- [x] Given a tariff code `E-1R-GO-VAR-22-10-14-C`, when the tariff type is determined, then it is `go`; Agile, Economy 7 and variable codes keep their types
- [x] Given elapsed days with usage skewed to the cheap window, when the Cost Forecast refreshes, then the projection uses the effective rate, not the flat or time-weighted average
- [x] Given no real elapsed day, when the Cost Forecast refreshes, then the projection uses today's time-weighted average rate
- [x] pre-commit (including pylint) passes

---

## Cheapest Time Window and Agile Prices follow the right tariff

**GitHub issue**: #724

**Blocked by**: None

**User stories**: 3, 4, 5, 7

### What to build

The Agile Prices panel always reads the Agile product (the account's own if Agile, else the newest Agile catalogue product). The Cheapest Time Window panel expands the current product's rates into half-hourly slots and uses the Agile forecast only when the current product is Agile. Update the dashboard JSON and `electricity-queries.md` together.

### Acceptance criteria

- [x] Given a Go account, when the Agile Prices panel loads, then it queries the Agile product, not the account's product
- [x] Given a Go account, when the Cheapest Time Window loads, then the Agile forecast is not used
- [x] Given an Agile account, when the Cheapest Time Window loads, then forecast fallback still applies
- [x] The JSON and the reference doc contain the same SQL

---

## Current Tariff panel

**GitHub issue**: #725

**Blocked by**: None

**User stories**: 6

### What to build

A full-width Stat panel at the top of the electricity dashboard showing the current electricity agreement's product display name (falling back to the tariff code). Existing panels shift down; `stat` is added to the required panel types; the reference doc gains an entry.

### Acceptance criteria

- [x] The dashboard has a stat panel titled "Current Tariff" with a unique id
- [x] Its query reads the current electricity agreement joined to `product`
- [x] `stat` is listed in the dashboard's required panel types
- [x] No panels overlap or exceed 24 columns
- [x] The reference doc documents the panel

---

# Issues: bugfix-project-unpublished-current-tariff-rates

## Share the per-slot rate rule between the Cost Forecast and a Tariff Projection

**Blocked by**: None

**User stories**: 9

### What to build

Prefactor with no behaviour change. The Cost Forecast's per-slot unit-rate rule becomes a public helper that other code can call. The rule is: the published rate of the product covering a half-hour, else the rate at the same local clock time on the latest fully published local day. Its search start, today tied to the billing period end, is generalised to "the last day needing a rate".

### Acceptance criteria

- [ ] The rule lives in one public helper, and the Cost Forecast uses it.
- [ ] Every existing Cost Forecast test, including the Go tests, passes unchanged.

---

## Write a Tariff Projection on every pricing refresh

**Blocked by**: #741

**User stories**: 1, 6, 7, 8, 10

### What to build

A new `tariff_projection` table, created by Schema Sync. At the end of each pricing refresh, octopus-app rewrites it in one transaction with half-hourly projected unit rates for the product of the electricity agreement in force, when that product is not Agile. It covers every half-hour from the end of published coverage to six days ahead, using the shared per-slot rule. The pricing refresh never fails because of the projection.

### Acceptance criteria

- [ ] Given Go rates published to 00:30 tomorrow (GMT), when the pricing refresh runs, then tomorrow's 00:30–05:30 local half-hours are projected at the off-peak rate and the rest at the peak rate, out to six days ahead.
- [ ] Given published Go rates, when the pricing refresh runs, then no published half-hour has a projected row.
- [ ] Given a projection across a clock change, when the pricing refresh runs, then the cheap half-hours stay at 00:30–05:30 local time.
- [ ] Given the current agreement is Agile, when the pricing refresh runs, then nothing is projected.
- [ ] Given no fully published day in the lookback, when the pricing refresh runs, then nothing is written, a warning names the product, and the rate sync still completes.
- [ ] Given a projection from an earlier refresh, when the pricing refresh runs again, then only the new projection remains.

---

## Cheapest Time Window fills unpublished half-hours from the Tariff Projection

**Blocked by**: #742

**User stories**: 1, 6

### What to build

The Cheapest Time Window query gains a projected-rates source. It is joined per half-hour on the covering agreement's product and used only where no published rate exists. The reference doc's section, query and schema list are updated to match.

### Acceptance criteria

- [ ] Given a Go half-hour with no published rate, the Cheapest Time Window prices it from `tariff_projection`.
- [ ] Given a half-hour with both a published and a projected rate, the published rate is used.
- [ ] The reference doc carries the same query as the dashboard, and its schema list includes `tariff_projection`.

---

## Current Tariff Prices panel

**Blocked by**: #742

**User stories**: 2, 3, 4, 5, 6

### What to build

A new time-series panel, "Current Tariff Prices" (id 23), at the top of the right-hand column. It shows half-hourly prices of the tariff covering each half-hour across the dashboard range, from published rates, then the Tariff Projection, then the Agile forecast. Published slots are drawn solid, projected or forecast slots dashed. The `+24h` and `now` markers also apply to it, and the panels below move down without overlaps. The reference doc gets a matching section.

### Acceptance criteria

- [ ] Panel 23 is a time series titled "Current Tariff Prices" at x 14, y 3, w 10, h 9, in `p/kwh` with the dashboard's price-band thresholds.
- [ ] Its query returns series `published`, `projected` and `forecast`, with the precedence published, then projection, then Agile forecast.
- [ ] `projected` and `forecast` are drawn dashed, and the lines step after each point.
- [ ] The `+24h` and `0h` annotation filters include panel 23.
- [ ] The Agile Prices panel and Cheapest Time Window move down by 9, and no panels overlap.
- [ ] The reference doc carries panel 23's query, matching the dashboard.

---

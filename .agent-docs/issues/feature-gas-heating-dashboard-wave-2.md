# Issues: feature-gas-heating-dashboard-wave-2

## D2-1 · The model chart — [#640](https://github.com/mholubinka1/home-monitoring/issues/640)

**Blocked by**: #629, #632

**User stories**: 1

### What to build

Daily gas against effective temperature as points with the latest model's fitted line; the legend carries baseload, threshold, slope and the estimated/confirmed label, and the date the model was last fitted is shown. Verify Grafana's XY chart suits; if not, record the fallback.

### Acceptance criteria

- [ ] Points and the fitted line match the latest model row.
- [ ] The legend shows baseload, threshold, slope and the label.
- [ ] The date the model was last fitted is visible.
- [ ] The chart type decision (or fallback) is documented.

---

## D2-2 · Where the gas went — [#641](https://github.com/mholubinka1/home-monitoring/issues/641)

**Blocked by**: #629, #632

**User stories**: 2, 3

### What to build

Stacked daily bars of five slices that sum to actual gas (baseload, expected heating, normal variation, possible, clear), with excess and avoidable each in two shades, legend totals for the chosen range, and a kWh or pounds switch.

### Acceptance criteria

- [ ] For any day the stacked slices sum to the day's gas.
- [ ] Legend totals match the verdict table for the selected range.
- [ ] Given the pounds option, then totals equal the day's cost.
- [ ] Away and unsettled days are neutral gaps.
- [ ] A not-needed day shows avoidable only when clearly warm.

---

## D2-3 · Baseload-adjusted efficiency trend — [#642](https://github.com/mholubinka1/home-monitoring/issues/642)

**Blocked by**: #629

**User stories**: 4

### What to build

A rolling 7-day line of (gas above baseload) per degree-day, hidden for windows with too few degree-days to measure.

### Acceptance criteria

- [ ] The line equals the 7-day sum of gas above baseload divided by the 7-day sum of degree-days.
- [ ] Windows below the minimum degree-days show a gap.
- [ ] The query and the minimum are documented.

---

## D2-4 · Weather-normalised year-on-year — [#643](https://github.com/mholubinka1/home-monitoring/issues/643)

**Blocked by**: #629, #624, #646, #647, #648

**User stories**: 5

### What to build

Weekly gas above baseload per degree-day, this year against the same weeks last year, complete weeks only. Settle in the branch's design session how a changed baseload is handled (recommended: subtract each period's own baseload from that period's warm days).

### Acceptance criteria

- [ ] Only complete weeks are shown.
- [ ] The baseload handling is decided and documented.
- [ ] Last year's weeks come from the same data and definitions as this year's.
- [ ] A week with too few degree-days is a gap.

---

## D2-5 · The saving from a lower setpoint — [#644](https://github.com/mholubinka1/home-monitoring/issues/644)

**Blocked by**: #629

**User stories**: 6

### What to build

Monthly bars of the estimated saving (pounds and kWh) from a thermostat 1 C lower (slope times needed days), with the average target on heating days as a line.

### Acceptance criteria

- [ ] The monthly saving equals the slope times that month's needed days (and the matching pounds).
- [ ] The average target line uses only days with a comfort target active.
- [ ] The estimate is labelled as an estimate.

---

## D2-6 · Expected gas for the next 7 days — [#645](https://github.com/mholubinka1/home-monitoring/issues/645)

**Blocked by**: #629, #635

**User stories**: 7

### What to build

The wave-1 daily gas panel gains lighter "expected gas" bars for today and the next 7 days from the forecast-day table.

### Acceptance criteria

- [ ] Expected bars match the forecast-day table.
- [ ] They are visually distinct from actual gas and labelled expected.
- [ ] Given no usable model, then no expected bars are shown.

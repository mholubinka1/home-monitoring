# Issues: feature-gas-heating-dashboard-wave-2

## D2-1 · The model chart — [#640](https://github.com/mholubinka1/home-monitoring/issues/640)

**Blocked by**: #629, #632

**User stories**: 1

### What to build

Daily gas against effective temperature as points with the latest model's fitted line; the panel title carries baseload, threshold, slope and the estimated/confirmed label (through a hidden summary variable), and the date the model was last fitted is shown there too. Verify Grafana's XY chart suits; if not, record the fallback.

### Acceptance criteria

- [x] Points and the fitted line match the latest model row (checked on the Pi, 27 October 2026).
- [ ] The panel title shows baseload, threshold, slope and the label (decided with the user: a hidden summary variable fills the title, not the legend; needs the real-Grafana look).
- [x] The date the model was last fitted is visible (in the title, checked on the Pi).
- [x] The chart type decision (or fallback) is documented (XY chart, Trend panel as the fallback).

---

## D2-2 · Where the gas went — [#641](https://github.com/mholubinka1/home-monitoring/issues/641)

**Blocked by**: #629, #632

**User stories**: 2, 3

### What to build

Stacked daily bars of five slices that sum to actual gas (baseload, expected heating, normal variation, possible, clear), with excess and avoidable each in two shades, legend totals for the chosen range, and a kWh or pounds switch.

### Acceptance criteria

- [x] For any day the stacked slices sum to the day's gas (checked on the Pi for all 660 judged days; a day below expectation shrinks the expected slices, decided with the user).
- [ ] Legend totals match the verdict table for the selected range (the legend is Grafana's sum of the series, which equal the verdict slices; needs the real-Grafana look).
- [x] Given the pounds option, then totals equal the day's cost (checked on the Pi for all 107 priced days).
- [x] Away and unsettled days are neutral gaps (away days are filtered out; unsettled days have no verdict row).
- [x] A not-needed day shows avoidable only when clearly warm (avoidable is the beyond-normal gas of a day with `needed = 0`, which is a day at or above the threshold's upper end; 14 October shows 1.69 kWh).

---

## D2-3 · Baseload-adjusted efficiency trend — [#642](https://github.com/mholubinka1/home-monitoring/issues/642)

**Blocked by**: #629

**User stories**: 4

### What to build

A rolling 7-day line of (gas above the moving baseload) per degree-day, hidden for windows with too few degree-days to measure.

### Acceptance criteria

- [x] The line equals the 7-day sum of gas above baseload divided by the 7-day sum of degree-days (15 January 2026: 257.629 / 53.497 = 4.816 by hand and by the panel query).
- [x] Windows below the minimum degree-days show a gap (under 1.5 degree-days a day on average, or under 5 judged days; the last 14 days of mild October are all gaps).
- [x] The query and the minimum are documented (queries document, "Heating Efficiency").

---

## D2-4 · Weather-normalised year-on-year — [#643](https://github.com/mholubinka1/home-monitoring/issues/643)

**Blocked by**: #650, #624, #646, #647, #648

**User stories**: 5

### What to build

Two lines, this year and last year (last year's weeks lined up 364 days earlier), of heating efficiency (kWh of heating gas per degree-day), week by week, read from `heating_week` and joined; a week appears only when both years' weeks have status `shown`. The title states how many weeks are shown and how many are left out by reason, and the baseload for each year; a dashboard switch shows every complete week. Labelled estimated, with a standing note that winter baseload is interpolated. The design and its evidence are in the week rules note.

### Acceptance criteria

- [ ] Only weeks with status `shown` in both years are plotted; others are gaps.
- [ ] The title shows the shown and left-out counts with reasons and the baseload for each year.
- [ ] Given the 'show every complete week' switch, then the guards are lifted and left-out weeks appear.
- [ ] Last year's weeks come from the same definitions as this year's (common degree-day threshold, each week's own moving baseload).
- [ ] The query joins this year's week to the week 364 days earlier and contains no guard or baseload logic.

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

The wave-1 daily gas panel gains lighter "expected gas" bars for the 7 days the model job forecasts (today and the next 6) from the forecast-day table.

### Acceptance criteria

- [x] Expected bars match the forecast-day table (checked on the Pi, kWh and pounds).
- [ ] They are visually distinct from actual gas and labelled expected (lighter fill and an 'expected' name; needs the real-Grafana look).
- [x] Given no usable model, then no expected bars are shown (the queries only read the latest usable model's forecast rows).

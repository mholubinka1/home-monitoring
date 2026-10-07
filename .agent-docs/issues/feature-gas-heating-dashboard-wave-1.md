# Issues: feature-gas-heating-dashboard-wave-1

## D1-1 · The new dashboard and the temperatures panel — [#TBD]

**Blocked by**: None

**User stories**: 1

### What to build

A new, separate dashboard file with its own title, uid, variables and defaults, its own documented queries and import steps (the existing dashboards and queries document are untouched), and the first panel: indoor, target (step line) and outdoor temperature as three lines.

### Acceptance criteria

- [ ] The dashboard JSON is valid with a unique title and uid, and the existing dashboard files are unchanged.
- [ ] Panel 1 shows indoor and target from the thermostat and outdoor from the weather (live preferred), in London time.
- [ ] The target is drawn as a step line; there is no heating-on strip or shading.
- [ ] The queries document records the panel's query and the import steps (Database field `home_monitoring`).
- [ ] The query was run read-only against live data and checked for a known day.

---

## D1-2 · Gas heatmap and monthly gas — [#TBD]

**Blocked by**: D1-1

**User stories**: 3, 4

### What to build

The gas heatmap by hour and weekday (London time, last 45 days, mirroring the electricity one) and the monthly gas total chart (this year against last, mirroring the electricity one).

### Acceptance criteria

- [ ] The heatmap shows average gas per time slot by weekday for the last 45 days, in London time.
- [ ] The monthly chart shows monthly gas totals for this year and last.
- [ ] Days without settled gas are gaps, not zeros.
- [ ] Both queries are documented and checked against the existing electricity equivalents' shape.

---

## D1-3 · The gas billing-period predictor — [#TBD]

**Blocked by**: D1-1

**User stories**: 5

### What to build

A bar gauge of the latest gas row of the cost forecast (cost so far against projected total), with the billing-period variables the existing "Billing Period Progress" panel uses.

### Acceptance criteria

- [ ] The gauge shows the latest gas actual-to-date and projected total.
- [ ] The billing-period variables are defined on the new dashboard.
- [ ] The query and the fact that it shows whatever the cost forecast currently computes are documented.

---

## D1-4 · Daily gas with outdoor temperature, kWh or pounds — [#TBD]

**Blocked by**: D1-1, BKF-2, FND-2

**User stories**: 2

### What to build

Daily gas bars with the daily mean outdoor temperature (complete days only) as a line on a second axis, a dashboard switch for kWh or pounds (pounds split into unit cost and standing charge from the gas agreement and rates), extended 7 days ahead with the forecast temperature.

### Acceptance criteria

- [ ] Given the kWh option, then bars show daily gas; given pounds, unit cost and standing charge appear as separate series.
- [ ] Outdoor temperature appears only for complete days; incomplete days are gaps.
- [ ] Days without settled gas are gaps, not zeros.
- [ ] The next 7 days show the forecast temperature.
- [ ] The pounds figure matches the cost forecast's daily gas cost for a known day.

---

## D1-5 · Boost minutes and the estimated extra gas cost — [#TBD]

**Blocked by**: D1-1

**User stories**: 6

### What to build

Bars of boost minutes per day (readings with boost active times the 2-minute interval) with the estimated extra gas cost as a second series: minutes / 60 times the boiler's gas input (kW, a dashboard variable) times the gas unit rate; minutes only until the variable is set.

### Acceptance criteria

- [ ] Given the boiler input is not set, then only minutes are shown.
- [ ] Given it is set, then the cost series equals minutes / 60 x kW x the day's unit rate.
- [ ] The series is labelled "estimated; assumes full output".
- [ ] Verified against the boosts recorded on 2026-10-06 (two short boosts).

---

## D1-6 · Minutes below target — [#TBD]

**Blocked by**: D1-1

**User stories**: 7

### What to build

Minutes per day with a comfort target active (target at or above a dashboard setting, default 15 C) and the room more than a dashboard margin (default 1.0 C) below it.

### Acceptance criteria

- [ ] A day on the frost setting counts zero regardless of temperature.
- [ ] A day with an active comfort target and the room below it by more than the margin counts those minutes.
- [ ] The two settings are dashboard variables with the stated defaults.

---

## D1-7 · Outdoor temperature band with solar — [#TBD]

**Blocked by**: D1-1, FND-2, BKF-1

**User stories**: 8

### What to build

Daily minimum, mean and maximum outdoor temperature (complete days) as a band, with sunshine and radiation on a second axis.

### Acceptance criteria

- [ ] The band shows min, mean and max for complete days only.
- [ ] Sunshine and radiation appear on a second axis.
- [ ] History appears from the backfill's start date.

---

## D1-8 · Validate the heating-on definition against gas — [#TBD]

**Blocked by**: None (waits for the gas to arrive)

**User stories**: 9

### What to build

An analysis, not a panel: compare the heating-on windows recorded on 2026-10-06 (about 19:14 to 19:21 and 19:24 to 19:27 UTC) with the half-hourly gas for the 19:00 to 19:30 UTC slot, and post the result on the issue. This replaces the half-hourly-with-shading panel proposed in #513.

### Acceptance criteria

- [ ] The result states whether gas in that slot rose in line with the thermostat demand.
- [ ] The conclusion on whether the heating-on definition needs changing is recorded.
- [ ] Issue #513 is closed only with the user's say-so.

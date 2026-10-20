# Issues: feature-gas-dashboard-review-tweaks

## TWK-1 · Forecast outdoor temperature on the temperatures panel — [#685](https://github.com/mholubinka1/home-monitoring/issues/685)

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

Move the forecast mean temperature from the daily gas panel onto the temperatures panel as a dashed continuation of the outdoor line that actual readings replace.

### Acceptance criteria

- [x] Panel 1 has a dashed `outdoor_forecast` series after the last outdoor reading; no forecast day is drawn where outdoor readings exist.
- [x] Panel 2 has no forecast query, override or description text.
- [x] The query is run read-only on the Pi and documented.

---

## TWK-2 · Monthly gas as a year-on-year ratio — [#686](https://github.com/mholubinka1/home-monitoring/issues/686)

**Blocked by**: None

**User stories**: 4, 5

### What to build

Replace the monthly gas bars and day-count dots with a month-by-month year-on-year percentage change, each month scaled to its full length.

### Acceptance criteria

- [x] The panel shows one percentage per month for the last 12 months, scaled to the month's full days.
- [x] No days series is shown.
- [x] Months without a usable comparator are left out.
- [x] The query is run read-only on the Pi and documented.

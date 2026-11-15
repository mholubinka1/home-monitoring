# Issues: feature/cost-comparison-panel

## Daily cost comparison panel

**GitHub issue**: #791

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11

### What to build

One new full-width time series panel at the bottom of the gas and heating dashboard, below the Schedule Today table, with three lines of daily heating-only gas cost in pounds for the last 30 completed London days (today excluded, whatever the dashboard range): Current Schedule (estimated) and Comfort Schedule (estimated) from the frozen day-ahead forecast rows of each scenario, and Actual from the heating-hour cost. A day is drawn per line only when every hour of that London day is present and priced; otherwise it is a gap. The panel reads only the two small forecast and heating-hour tables. The queries doc gains a section for the panel, the glossary's Gas Heating Dashboard entry mentions it, and the dashboard tests pin it.

### Acceptance criteria

- [x] Given the dashboard file, when it is checked, then the panel has a unique id, is a time series, is full width, sits below the Schedule Today table, and the panels still tile the grid without gaps or overlaps.
- [x] Given the panel, when its series are checked, then it has current_cost, plan_cost and actual_cost named "Current Schedule (estimated)", "Comfort Schedule (estimated)" and "Actual", in pounds, with the two estimates dashed, Actual solid, and gaps not joined.
- [x] Given the panel's SQL, when it is checked, then the estimates read only day_ahead rows of the current and plan scenarios of room_forecast_hour and never live rows, and Actual reads heating_hour of both sources.
- [x] Given the panel, when its window is checked, then the query covers the 30 London days ending yesterday, excludes the current day, and the panel has a 31-day relative time override.
- [x] Given the panel's SQL, when it is checked, then each series keeps a London day only when its count of priced hours equals the hours in that day (23, 24 or 25).
- [x] Given the panel's SQL, when it is checked, then it reads only room_forecast_hour and heating_hour.
- [x] Given the dashboard file, when the dashboard-wide tests run, then they still pass (chart-only panel types, distinct panel ids, every variable used).

---

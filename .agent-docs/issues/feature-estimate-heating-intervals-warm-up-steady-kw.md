# Issues: feature-estimate-heating-intervals-warm-up-steady-kw

## Estimate heating intervals with a warm-up and a steady kW

**GitHub issue**: #818

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5, 11, 12

### What to build

Estimated Heating Intervals are priced at a learned Warm-up kW for their minutes in the first 20 minutes of a Heating Run and a learned Steady kW for the rest (ADR-0039). The job splits each half-hour's on-minutes by run position (a run carries on through a single missed or OFF poll, and polls are read from 25 minutes before the window), stores the early on-minutes, learns both figures by least squares from the measured rows of the last 28 days, and falls back to the single Effective Boiler kW when the evidence is thin or a figure is not positive. Panel 3's description says how estimates are made. The room forecast is unchanged.

### Acceptance criteria

- [x] Given 10 or more measured heating half-hours whose gas comes from a known 18 kW warm-up and 6 kW steady draw, when a half-hour holding a run's first 20 minutes has no gas yet, then it is estimated at 18 kW for those minutes and 6 kW for the rest.
- [x] Given that learned history and a Heating Run that started 40 minutes before a half-hour, when that half-hour is estimated, then all its minutes on are priced at the Steady kW.
- [x] Given a Heating Run that crosses a half-hour boundary, when both half-hours are computed, then only the run's first 20 minutes, on either side of the boundary, count as early on-minutes.
- [x] Given a Heating Run with a single OFF poll inside it, when it is computed, then the run carries on and no new warm-up starts; and given two OFF polls in a row, then the next ON poll starts a new warm-up.
- [x] Given a Heating Run that started before the recompute window, when the job runs, then the window's first half-hour is split by the run's real start.
- [x] Given fewer than 10 measured heating half-hours with a split, when a half-hour is estimated, then it uses the single learned kW, or the 12 kW default, as today.
- [x] Given measured history that implies a zero or negative Warm-up or Steady kW, or where every half-hour has the same split, when a half-hour is estimated, then it uses the single learned kW.
- [x] Given any computed half-hour, when it is stored, then it carries its early on-minutes.
- [x] Given the gas heating dashboard, when panel 3 "Heating Cost per Half-Hour" is read, then its description says estimates use a warm-up kW for the first 20 minutes of each heating run and a steady kW after.

---

## Keep a frozen Heating Estimate on every half-hour

**GitHub issue**: #819

**Blocked by**: #818

**User stories**: 6, 7, 8

### What to build

Every Heating Interval stores its Heating Estimate in a new nullable `estimated_heating_kwh` column. An estimated row's Heating Estimate is its heating kWh. When the gas arrives and the row turns measured, it keeps the Heating Estimate of the stored row it replaces; a row measured from its first computation gets the estimate from that run's figures.

### Acceptance criteria

- [ ] Given a heating half-hour with no gas yet, when the job runs, then its Heating Estimate equals its estimated heating kWh.
- [ ] Given a half-hour stored as estimated, when its gas arrives and a later run's learned figures would estimate it differently, then the measured row keeps the earlier Heating Estimate.
- [ ] Given a half-hour whose gas is already there when it is first computed (a backfill), when the job runs, then it gets the Heating Estimate of that run's figures.
- [ ] Given a heating-off half-hour, when the job runs, then its Heating Estimate is 0.

---

## Fill the warm-up split of rows stored before the change

**GitHub issue**: #820

**Blocked by**: #818

**User stories**: 9, 10

### What to build

At the start of a refresh, stored Heating Intervals in the 28 days before the recompute window that have no early on-minutes get them, worked out once from the kept thermostat polls. Only that column is written. Once every row has the split, the fill reads no polls.

### Acceptance criteria

- [ ] Given stored half-hours in the last 28 days without early on-minutes and their thermostat polls, when the job runs, then those half-hours get early on-minutes from the polls.
- [ ] Given those filled half-hours, when the fill has run, then their measured kWh, heating kWh, cost, source and computed_at are unchanged and their Heating Estimate stays empty.
- [ ] Given 10 or more measured heating half-hours stored before the change, when the job first runs, then an estimated half-hour is priced at the Warm-up and Steady kW learned from them.
- [ ] Given every stored half-hour already has early on-minutes, when the job runs, then the fill reads no thermostat polls.

---

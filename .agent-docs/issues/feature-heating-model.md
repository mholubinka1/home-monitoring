# Issues: feature-heating-model

> Wave A (MOD-1, MOD-2: #672) and wave B (MOD-3, MOD-4, MOD-7: #674) are merged. Wave C, PR 1 — MOD-5 and MOD-8 (the job, the tables, the weekly rows) — is built and in review; PR 2 is MOD-6 (the cost forecast adopts the model), started only after PR 1 is merged.

## MOD-1 · Complete local-day temperatures at the current location, and the effective temperature — [#625](https://github.com/mholubinka1/home-monitoring/issues/625)

**Blocked by**: #620, #623

**User stories**: 1, 12

### What to build

octopus-app can read, for each local day (the Europe/London time-zone calendar day, at the current Weather Location, taken from the location key on the newest stored observation: the postcode's location unless an explicit `location` config is set), the mean outdoor temperature taken only when every hour of that day is present (23, 24 or 25 on daylight-saving days), preferring live over archive per hour, and can combine today's and yesterday's mean into the effective temperature for a given weight. Incomplete days are absent, never partial.

### Acceptance criteria

- [x] Given 24 hourly readings for a local day at the current location, then its mean is returned; given one missing hour, then the day is absent.
- [x] Given readings stored under a different location key, then they are ignored (a changed postcode or override never mixes places).
- [x] Given a spring-forward day (23 hours) and an autumn day (25), then each counts as complete with the right number of hours.
- [x] Given live and archive rows for the same hour, then the live value is used.
- [x] Given a weight, then the effective temperature equals the stated blend of today's and yesterday's means, and is absent if either is.
- [x] The reader tolerates the weather tables not existing yet (as the existing one does).

---

## MOD-2 · Fit the model from daily gas and effective temperature — [#626](https://github.com/mholubinka1/home-monitoring/issues/626)

**Blocked by**: None (a pure function; real inputs come from #625)

**User stories**: 1, 6, 12

### What to build

Pure functions that, from daily gas and effective temperatures, compute a **moving baseload** (the mean of warm-day gas over a trailing 120-day window (the 120 days ending on that day), at least 8 warm days to count as measured, joined by a straight line across winter gaps and held at the last measured value for the newest days and at the first measured value for any days before it, each point labelled measured, interpolated or held) and fit slope and threshold by searching a grid of thresholds and blend weights, with the slope from least squares through the origin on the gas above baseload, treats zero-gas rows as missing, excludes away days (gas below about a third of baseload) and refits once, and reports whether the fit is usable and why not.

### Acceptance criteria

- [x] Given synthetic days with a planted slope and threshold and a baseload that drifts and swings with the seasons, then the fit recovers the slope and threshold, and the moving baseload follows the planted baseload where it can be measured.
- [x] Given a window with fewer than 8 warm days, then the baseload there is interpolated (labelled) between the nearest measured estimates, and the newest days hold the last measured value.
- [x] Given days before the first measured estimate, then they hold that first measured value (labelled held).
- [x] The baseload series covers every day with data, not only the rolling window.
- [x] Given zero-gas rows, then they are treated as missing; given a partial day (fewer half-hours than expected), then it is excluded.
- [x] Given a stretch of very low-use days, then they are excluded, shown as away, and the baseload is not pulled down.
- [x] Given too few days, too few on either side of the threshold, R-squared below 0.5, a threshold at the edge of the search, or a non-positive slope, then the result is unusable with the matching reason; given no combination that can be scored (no day below any threshold, or no measurable baseload), then the reason is "no fit", or too few days when fewer than about 150 window days have both gas and an effective temperature.
- [x] The blend weight is bounded (0 to 0.8).

---

## MOD-3 · Judge every day — [#627](https://github.com/mholubinka1/home-monitoring/issues/627)

**Blocked by**: #626

**User stories**: 2, 3, 4, 5, 12

### What to build

Pure functions that give each day an expected gas, a scaled noise margin (growing with the heating need), a needed / not-needed decision using the threshold's upper uncertainty end (from resampling), and the slices: baseload (the moving baseload for that day), expected heating, normal variation, possible (beyond 1 standard deviation, up to 2) and clear (beyond 2), counting only the part beyond the margin, so slices sum exactly to actual gas. Plus the setpoint saving (slope times needed days).

### Acceptance criteria

- [x] Given any day, then its slices sum exactly to its actual gas.
- [x] Given an overshoot of 1.4 margins, then only the part beyond 1 margin is "possible" and none is "clear".
- [x] Given a day inside the threshold's uncertainty range, then it is judged needed (any overshoot is excess, not avoidable).
- [x] Given a clearly warm day with gas above baseload by more than the margin, then the overshoot is avoidable.
- [x] Given an away day, then it is neutral.
- [x] Given a 1 C lower setpoint over a period, then the saving equals the slope times the needed days.

---

## MOD-4 · Thermostat check and the estimated/confirmed label — [#628](https://github.com/mholubinka1/home-monitoring/issues/628)

**Blocked by**: #626

**User stories**: 7, 12

### What to build

From `heating_status`, classify each London day with enough readings (about 90% of 720) as a heating day (at least 30 minutes of scheduled, non-boost demand) or not; find the effective temperature that best separates the two; label the model "confirmed" when it is within about 1.5 C of the gas-based threshold with about 15 days of evidence on each side, otherwise "estimated".

### Acceptance criteria

- [x] Given boost-only demand on a warm day, then it does not make that day a heating day.
- [x] Given a day with too few readings, then it is not classified.
- [x] Given clearly separated heating and non-heating days, then the thermostat-side threshold lies between them.
- [x] Given agreement within the tolerance and enough evidence on both sides, then the label is "confirmed"; otherwise "estimated".
- [x] With little heating history (as now), the label is "estimated".

---

## MOD-5 · The daily job and the tables — [#629](https://github.com/mholubinka1/home-monitoring/issues/629)

**Blocked by**: #625, #627, #628, #646, #647

**User stories**: 8, 10

### What to build

A daily octopus-app job (recorded in `job_run`) that refits weekly, recomputes every day's verdict with the latest model, and projects 7 days from the forecast daily mean, writing `heating_model`, `heating_day_verdict`, `heating_forecast_day` (created additively by Schema Sync); the weekly efficiency table is written by the next issue. It also runs once at startup when there is no model and enough data.

### Acceptance criteria

- [x] Given no model and enough data, then a model row and verdict rows are written at startup.
- [x] Given a model fitted under 7 local days ago, then the daily run recomputes verdicts without refitting; 7 or more, then it refits (and while no usable model exists it refits every run).
- [x] Given an unusable fit, then a model row with the reason is written and no misleading verdicts are.
- [x] Given the forecast table, then forecast-day rows hold expected gas for the next 7 days.
- [x] Each daily run records success or failure in `job_run` (the startup run, like the other startup syncs, only logs); Schema Sync creates the tables on an existing database.

---

## MOD-6 · The cost forecast adopts the model — [#630](https://github.com/mholubinka1/home-monitoring/issues/630)

**Blocked by**: #629

**User stories**: 9

### What to build

The cost forecast takes each remaining day's expected gas from the latest usable model and forecast-day table, falling back to today's flat-average behaviour for the whole period when there is none. The old regression, its constants and its now-unused weather reader are removed. The pull request shows the old and new projections side by side.

### Acceptance criteria

- [ ] Given a usable model, then the projected gas cost uses the model's expected gas for the remaining days.
- [ ] Given no usable model, then the projection equals the existing flat-average result.
- [ ] The old regression code and tests are removed and replaced by model-based tests.
- [ ] The PR description lists the old and new projected total for the current period.
- [ ] The Gas Cost Forecast glossary entry is updated.

---

## MOD-7 · The plain-language explainer — [#631](https://github.com/mholubinka1/home-monitoring/issues/631)

**Blocked by**: #627, #628

**User stories**: 11

### What to build

`docs/heating-model.md`: an explainer for people who are not data scientists, following the outline and verified concept links in the spec: what it is for, the one-line idea, baseload, threshold, slope, why yesterday matters, judging a day (expected, normal wobble, possible, clear), why totals are a floor, away days, estimated vs confirmed, what the model cannot tell you, how to read the dashboard panels, a glossary and further reading, with real numbers from the household's data for one worked example.

### Acceptance criteria

- [x] Every item in the spec's outline is present, in plain words, with at most one formula, explained in words.
- [x] Each technical idea has a "learn more" link from the spec's verified list (or a better verified one).
- [x] The worked example matches the implemented model's behaviour.
- [x] A reader who is not a data scientist can answer: why is my threshold what it is, what does "possible" mean, and why is the total a floor.
- [x] The repository's Markdown link check passes.

---

## MOD-8 · Weekly efficiency rows with guards and reasons — [#650](https://github.com/mholubinka1/home-monitoring/issues/650)

**Blocked by**: #629

**User stories**: 4, 13

### What to build

The model job also writes `heating_week`, one row per Monday-to-Sunday week over the whole history that has at least one gas day (a week with no gas at all is a gap): complete gas days, per-day averages of gas, degree-days (against the current model's threshold, on the effective temperature, for every year) and baseload (the moving baseload), heating gas above baseload, the efficiency (heating gas per degree-day), and a status that is `shown` or the reason the week is left out: too few days (under 5 complete), too mild (under 1.5 degree-days a day) or heating off (under 0.5 kWh a day of heating gas). The three guard values are named constants.

### Acceptance criteria

- [x] Given a week with 5 complete days, then its per-day averages are used and its status is `shown` if the other guards pass.
- [x] Given a week with 4 complete days, then its status is "too few days"; under 1.5 degree-days a day, "too mild"; under 0.5 kWh a day of heating gas, "heating off".
- [x] Given two years, then both use the same degree-day threshold and each week's own moving baseload.
- [x] Given a week with an away or incomplete day, then that day is excluded from the averages (and counts against the 5).
- [x] The table covers the whole history with data, is rebuilt consistently on each refit, and Schema Sync creates it on an existing database.

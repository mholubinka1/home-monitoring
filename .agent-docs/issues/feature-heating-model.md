# Issues: feature-heating-model

## MOD-1 · Complete London-day temperatures and the effective temperature — [#625](https://github.com/mholubinka1/home-monitoring/issues/625)

**Blocked by**: #620, #623

**User stories**: 1, 12

### What to build

octopus-app can read, for each London day, the mean outdoor temperature taken only when every hour of that local day is present (23, 24 or 25 on daylight-saving days), preferring live over archive per hour, and can combine today's and yesterday's mean into the effective temperature for a given weight. Incomplete days are absent, never partial.

### Acceptance criteria

- [ ] Given 24 hourly readings for a London day, then its mean is returned; given one missing hour, then the day is absent.
- [ ] Given a spring-forward day (23 hours) and an autumn day (25), then each counts as complete with the right number of hours.
- [ ] Given live and archive rows for the same hour, then the live value is used.
- [ ] Given a weight, then the effective temperature equals the stated blend of today's and yesterday's means, and is absent if either is.
- [ ] The reader tolerates the weather tables not existing yet (as the existing one does).

---

## MOD-2 · Fit the model from daily gas and effective temperature — [#626](https://github.com/mholubinka1/home-monitoring/issues/626)

**Blocked by**: None (a pure function; real inputs come from #625)

**User stories**: 1, 6, 12

### What to build

A pure function that, from daily gas and effective temperatures, fits baseload, slope and threshold by searching a grid of thresholds and blend weights with least squares, treats zero-gas rows as missing, excludes away days (gas below about a third of baseload) and refits once, and reports whether the fit is usable and why not.

### Acceptance criteria

- [ ] Given synthetic days with a planted baseload, slope and threshold plus noise, then the fit recovers them within a stated tolerance.
- [ ] Given zero-gas rows, then they are treated as missing; given a partial day (fewer half-hours than expected), then it is excluded.
- [ ] Given a stretch of very low-use days, then they are excluded, shown as away, and the baseload is not pulled down.
- [ ] Given too few days, too few on either side of the threshold, R-squared below 0.5, a threshold at the edge of the search, or a non-positive slope, then the result is unusable with the matching reason.
- [ ] The blend weight is bounded (0 to 0.8).

---

## MOD-3 · Judge every day — [#627](https://github.com/mholubinka1/home-monitoring/issues/627)

**Blocked by**: #626

**User stories**: 2, 3, 4, 5, 12

### What to build

Pure functions that give each day an expected gas, a scaled noise margin (growing with the heating need), a needed / not-needed decision using the threshold's upper uncertainty end (from resampling), and the slices: baseload, expected heating, normal variation, possible (beyond 1 standard deviation, up to 2) and clear (beyond 2), counting only the part beyond the margin, so slices sum exactly to actual gas. Plus the setpoint saving (slope times needed days).

### Acceptance criteria

- [ ] Given any day, then its slices sum exactly to its actual gas.
- [ ] Given an overshoot of 1.4 margins, then only the part beyond 1 margin is "possible" and none is "clear".
- [ ] Given a day inside the threshold's uncertainty range, then it is judged needed (any overshoot is excess, not avoidable).
- [ ] Given a clearly warm day with gas above baseload by more than the margin, then the overshoot is avoidable.
- [ ] Given an away day, then it is neutral.
- [ ] Given a 1 C lower setpoint over a period, then the saving equals the slope times the needed days.

---

## MOD-4 · Thermostat check and the estimated/confirmed label — [#628](https://github.com/mholubinka1/home-monitoring/issues/628)

**Blocked by**: #626

**User stories**: 7, 12

### What to build

From `heating_status`, classify each London day with enough readings (about 90% of 720) as a heating day (at least 30 minutes of scheduled, non-boost demand) or not; find the effective temperature that best separates the two; label the model "confirmed" when it is within about 1.5 C of the gas-based threshold with about 15 days of evidence on each side, otherwise "estimated".

### Acceptance criteria

- [ ] Given boost-only demand on a warm day, then it does not make that day a heating day.
- [ ] Given a day with too few readings, then it is not classified.
- [ ] Given clearly separated heating and non-heating days, then the thermostat-side threshold lies between them.
- [ ] Given agreement within the tolerance and enough evidence on both sides, then the label is "confirmed"; otherwise "estimated".
- [ ] With little heating history (as now), the label is "estimated".

---

## MOD-5 · The daily job and the three tables — [#629](https://github.com/mholubinka1/home-monitoring/issues/629)

**Blocked by**: #625, #627, #628

**User stories**: 8, 10

### What to build

A daily octopus-app job (recorded in `job_run`) that refits weekly, recomputes every day's verdict with the latest model, and projects 7 days from the forecast daily mean, writing `heating_model`, `heating_day_verdict` and `heating_forecast_day` (created additively by Schema Sync). It also runs once at startup when there is no model and enough data.

### Acceptance criteria

- [ ] Given no model and enough data, then a model row and verdict rows are written at startup.
- [ ] Given a model fitted under 7 days ago, then the daily run recomputes verdicts without refitting; older, then it refits.
- [ ] Given an unusable fit, then a model row with the reason is written and no misleading verdicts are.
- [ ] Given the forecast table, then forecast-day rows hold expected gas for the next 7 days.
- [ ] Each run records success or failure in `job_run`; Schema Sync creates the tables on an existing database.

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

- [ ] Every item in the spec's outline is present, in plain words, with at most one formula, explained in words.
- [ ] Each technical idea has a "learn more" link from the spec's verified list (or a better verified one).
- [ ] The worked example matches the implemented model's behaviour.
- [ ] A reader who is not a data scientist can answer: why is my threshold what it is, what does "possible" mean, and why is the total a floor.
- [ ] The repository's Markdown link check passes.

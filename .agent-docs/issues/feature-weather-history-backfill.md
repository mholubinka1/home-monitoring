# Issues: feature-weather-history-backfill

## BKF-1 · Backfill hourly weather history from the archive — [#623](https://github.com/mholubinka1/home-monitoring/issues/623)

**Blocked by**: #620, #621

**User stories**: 1, 2, 3, 6

### What to build

A re-runnable hive-app command that fills `weather_observation` with hourly weather (the stored variables plus solar) from 2024-07-24 (overridable) using Open-Meteo's historical archive in sensible chunks, at the cached Weather Location, in UTC. Every row is labelled `open-meteo-archive` and written with the keyed upsert, so a second run changes nothing and live rows are never touched.

### Acceptance criteria

- [ ] Given a date range, when the command runs, then hourly rows with the archive label are stored for every hour returned.
- [ ] Given a second run over the same range, then the row count is unchanged and values are replaced, not duplicated.
- [ ] Given live (`open-meteo`) rows for some of the same hours, then they are untouched.
- [ ] Given a chunk that fails after retries, then earlier chunks stay intact and the message says which range to repeat.
- [ ] Given no cached Weather Location, then it stops with a clear message.
- [ ] Given a different current location, then rows are stored under that location's key and the earlier location's rows are untouched.

---

## BKF-2 · Fill the recent days and report completeness — [#624](https://github.com/mholubinka1/home-monitoring/issues/624)

**Blocked by**: #623

**User stories**: 4, 5

### What to build

For the recent days the archive has not reached yet, fetch from the forecast service's `past_days` (up to 92) first, so the archive's reanalysis later replaces overlapping hours, and never store future hours as observations. After the run, print rows per month, the count of complete London days (23, 24 or 25 hourly readings, live preferred over archive per hour) and the days with missing hours.

### Acceptance criteria

- [ ] Given days beyond the archive's reach, then their hours are stored from `past_days` with the archive label.
- [ ] Given a later run when the archive has the same hours, then the archive values replace them.
- [ ] Given a response that includes hours in the future, then none of them are stored.
- [ ] Given a daylight-saving day, then the report counts 23 or 25 hours correctly.
- [ ] Given a day with a missing hour, then it is listed as incomplete in the report.

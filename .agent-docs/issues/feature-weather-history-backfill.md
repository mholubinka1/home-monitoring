# Issues: feature-weather-history-backfill

> Work complete — [PR #664](https://github.com/mholubinka1/home-monitoring/pull/664) ready to merge. Running the backfill on the Pi is a separate, asked-first step after deploy.

## BKF-1 · Backfill hourly weather history from the archive — [#623](https://github.com/mholubinka1/home-monitoring/issues/623)

**Blocked by**: #620, #621

**User stories**: 1, 2, 3, 6

### What to build

A re-runnable hive-app command that fills `weather_observation` with hourly weather (the stored variables plus solar) from 2024-07-24 (overridable) using Open-Meteo's historical archive in sensible chunks, at the cached Weather Location, in UTC. Every row is labelled `open-meteo-archive` and written with the keyed upsert, so a second run changes nothing and live rows are never touched.

### Acceptance criteria

- [x] Given a date range, when the command runs, then hourly rows with the archive label are stored for every hour returned.
- [x] Given a second run over the same range, then the row count is unchanged and values are replaced, not duplicated.
- [x] Given live (`open-meteo`) rows for some of the same hours, then they are untouched.
- [x] Given a chunk that fails after retries, then earlier chunks stay intact and the message says which range to repeat.
- [x] Given no cached Weather Location, then it stops with a clear message.
- [x] Given a different current location, then rows are stored under that location's key and the earlier location's rows are untouched.

---

## BKF-2 · Fill the recent days and report completeness — [#624](https://github.com/mholubinka1/home-monitoring/issues/624)

**Blocked by**: #623

**User stories**: 4, 5

### What to build

Fill up to the latest completed hour from the archive alone (it returns provisional values for the newest days; decided at build on 2026-10-10, replacing the planned forecast-service `past_days` source), never storing future hours as observations. After the run, print rows per month, the count of complete London days (23, 24 or 25 hourly readings, live preferred over archive per hour) and the days with missing hours.

### Acceptance criteria

- [x] Given the archive returns hours up to the latest completed hour, then they are all stored with the archive label.
- [x] Given a later run when the archive's values for the same hours have changed, then the new values replace them.
- [x] Given a response that includes hours in the future, then none of them are stored.
- [x] Given a daylight-saving day, then the report counts 23 or 25 hours correctly.
- [x] Given a day with a missing hour, then it is listed as incomplete in the report.

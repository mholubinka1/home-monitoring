# Issues: feature-weather-backfill-at-startup

> Work complete — merged in #666 and verified on the Pi on 2026-10-12 (the backfill stored 19,444 hours, complete days 810 of 810).

## WBS-1 · Backfill weather history automatically at hive-app startup — [#665](https://github.com/mholubinka1/home-monitoring/issues/665)

**Blocked by**: None

**User stories**: 1, 2, 3, 4

### What to build

At hive-app startup, check how far back stored weather reaches for the current Weather Location; if it does not reach the first hour of 2024-07-24, run the existing weather history backfill in the background, recorded in `job_run` as `weather_history_backfill`, otherwise skip. Only a cached location is used; startup never geocodes. A successful run logs the hours stored and the complete-day count. The README and ADR-0029 describe the automatic run.

### Acceptance criteria

- [x] Given no stored weather for the current location, when hive-app starts, then the backfill runs in the background from 2024-07-24 and is recorded in `job_run` as a success.
- [x] Given stored weather reaching back to the first hour of 2024-07-24, then startup skips the backfill and makes no request.
- [x] Given stored weather that starts later than that, then the backfill runs.
- [x] Rows under a different location key do not count towards how far back the history reaches.
- [x] Given no cached Weather Location, then startup skips the backfill without geocoding or requesting anything.
- [x] Given a run that keeps failing, then it is retried with backoff, each failure is recorded in `job_run`, and hive-app keeps starting.
- [x] A successful run logs the hours stored and "complete days: N of M", with no coordinates.
- [x] The README says the backfill runs at startup, keeps the command for `--start` re-runs and restricts the "re-run a week later" advice to after a wipe or restore; ADR-0029 gets a dated note.

---

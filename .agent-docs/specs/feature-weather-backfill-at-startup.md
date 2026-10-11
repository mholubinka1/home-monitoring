# Weather history backfill at startup

## Problem Statement

The weather history backfill (#664) is a hand-run command. Someone has to remember to run it once on the Pi, and again after any database wipe or restore, or the heating model and the year-on-year panels quietly have no weather before 2026-10-06. The gas summary already avoids this with a data-gated startup backfill (#660); weather should behave the same way.

## Solution

At hive-app startup, check how far back the stored weather goes for the current Weather Location. If it does not reach the start of the gas history (2024-07-24), run the existing backfill in the background, recorded in `job_run`; otherwise skip. The first restart after deploy fills the history by itself, later restarts skip, and a wiped or restored database refills itself. The hand-run command stays for re-runs from a chosen date.

## User Stories

1. As the account holder, I want weather history filled without anyone remembering a command, so that the model and panels always have two winters of weather.
2. As an operator, I want a wiped or restored database to refill its weather history on the next restart, so that recovery needs no extra step.
3. As an operator, I want each startup backfill recorded in `job_run` and summarised in the log, so that I can see whether it ran and how complete the history is.
4. As an operator, I want startup to skip the backfill once the history is in place, so that daily restarts do not re-download two years of weather.

## Implementation Decisions

- **Gate on the data.** At startup, the oldest stored observation hour for the current location key (either source) is compared with the first hour of the default start day (2024-07-24, its first hour-ending stamp per ADR-0029). Later or none: run. Otherwise skip with an info log. Like the gas gate, this checks how far back the data reaches, not gaps within it.
- **Cached location only.** The gate reads the cached Weather Location; with none (a brand-new database before the hourly weather job has resolved one) it skips with an info log and never geocodes at startup. The next restart (Watchtower restarts hive-app daily) runs it.
- **Background, recorded, retried.** Runs in a background thread through hive-app's existing background-with-backoff wrapper under the job name `weather_history_backfill`, from 2024-07-24 to today, reusing `WeatherHistoryBackfill`. A failed run retries as a whole (the backfill is idempotent). Changed during review (2026-10-11): the backfill now fetches chunks newest first, so a run that keeps failing leaves the oldest stretch missing and the gate retries at the next start; its "Repeat from" message names the run's first day, since older chunks were never fetched.
- **Log summary.** On success it logs the hours stored and "complete days: N of M" up to yesterday; the full incomplete-day list stays in the command's printed report. Logs carry no coordinates (the location key only if needed; prefer not).
- **Docs.** README: startup does this automatically; the command remains for `--start` re-runs; restrict the "re-run a week later" advice to after a wipe or restore (otherwise live rows already cover the provisional recent days and are preferred). ADR-0029 dated note.

## Testing Decisions

- Seam: the new startup function in hive-app's `main.py`, with the SQLite fixture and HTTP mocked at the boundary (as the existing backfill tests do), mirroring octopus-app's `run_backfill_at_startup` tests.
- Good tests: no stored weather → runs and records `job_run` success; history reaching 2024-07-24 → skipped, no request; history starting later → runs; rows under a different location key do not count; no cached location → skipped, no request; a failing run is retried and recorded as failures without crashing startup; the success log line carries the completeness summary and no coordinates.
- Prior art: `apps/octopus-app/tests/test_refresh_scheduling.py` (startup backfill tests) and `apps/hive-app/tests/test_weather_history_backfill.py`.

## Out of Scope

- Detecting or filling gaps in the middle of the history.
- Refetching the archive's provisional recent days.
- Making the local-day timezone configurable (#663).

## Further Notes

- On the Pi today no archive rows exist, so the first 03:00 UTC restart after deploy runs the backfill (about 19,500 hours, three archive requests).

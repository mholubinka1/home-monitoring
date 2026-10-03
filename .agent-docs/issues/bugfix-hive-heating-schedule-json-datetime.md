# Issues: bugfix-hive-heating-schedule-json-datetime

> Work complete — [PR #571](https://github.com/mholubinka1/home-monitoring/pull/571) ready to merge.
> Verified on SQLite only: after deploy, confirm `heating_status` fills and
> `job_run` shows `heating_refresh` successes on the live database.

## hive-app: store the real heating schedule (datetimes) in the JSON column — [#570](https://github.com/mholubinka1/home-monitoring/issues/570)

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5

### What to build

Make the heating schedule JSON-safe where it is written, so heating polls can
be stored. Live evidence: `heating_status` has 0 rows ever; `job_run` shows
2239 `heating_refresh` failures and 0 successes; after auth recovered the
failure is `Object of type datetime is not JSON serializable` on the
`schedule` JSON column. The real apyhiveapi schedule is
`{"now": {"value": {"target": 7}, "start": 720, "Start_DateTime": <datetime>,
"End_DateTime": <datetime>}, "next": {...}, "later": {...}}`; tests only ever
used string-only schedules. At `MariaDBClient.write_heating_status`,
recursively convert `datetime`/`date` values in the schedule to ISO-8601
strings and leave everything else unchanged; do not mutate the caller's data;
keep key names and shape. `polled_at` and `boost_ends_at` stay real datetime
columns. Note the finding on the weather tables (no JSON columns) and add a
dated note to ADR-0017.

### Acceptance criteria

- [x] Given a real-shaped schedule containing datetimes, the poll is written
      and the stored schedule has ISO-8601 strings with numeric fields and
      keys intact.
- [x] Given a schedule with no datetimes, it round-trips unchanged.
- [x] Given `HeatingRetriever.refresh` with a real-shaped schedule, it
      persists without error.
- [x] `polled_at` and `boost_ends_at` are still stored as datetimes, and the
      caller's schedule is not mutated.
- [x] ADR-0017 has a dated note about ISO-8601 datetimes in the stored
      schedule.

---

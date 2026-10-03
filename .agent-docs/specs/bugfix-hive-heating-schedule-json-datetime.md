# hive-app: store the real heating schedule, which contains datetimes, in the JSON column

## Problem Statement

hive-app has never stored a single heating reading. In production `heating_status` has zero rows, and `job_run` records over two thousand `heating_refresh` failures and no successes. Once Hive authentication was recovered, the failure showed its true cause: `Object of type datetime is not JSON serializable`, raised when the poll is written. The poll fetches fine; only the write fails. CI never caught it because every test used a string-only schedule.

## Solution

The heating schedule is made JSON-safe where it is written: any datetime inside it is stored as an ISO-8601 string, and everything else (numbers, strings, booleans, nulls, nested dictionaries and lists) is stored exactly as it is. The real shape of the schedule from apyhiveapi is `{"now": {"value": {"target": 7}, "start": 720, "Start_DateTime": <datetime>, "End_DateTime": <datetime>}, "next": {...}, "later": {...}}`, and the stored keys and the now/next/later layout do not change.

## User Stories

1. As the operator, I want each heating poll to be written to `heating_status`, so that heating data is actually collected.
2. As the operator, I want the schedule's start and end times kept as readable ISO-8601 timestamps, so that Grafana can read them with JSON-path functions.
3. As the operator, I want the schedule's numeric fields and key names unchanged, so that nothing reading the column breaks.
4. As the operator, I want `polled_at` and `boost_ends_at` to stay real datetime columns, so that time filtering keeps working.
5. As a maintainer, I want a test that uses the real schedule shape, so that this class of bug cannot ship unnoticed again.

## Implementation Decisions

- **Where.** The fix is at the persist boundary, `MariaDBClient.write_heating_status`, where JSON-ness matters, so every caller is safe. `HeatingStatus.schedule` keeps its honest type (`dict[str, Any]`).
- **How.** A small recursive converter walks dictionaries and lists and turns `datetime` and `date` values into `isoformat()` strings, leaving every other value untouched. It does not mutate the caller's `HeatingStatus`.
- **Unchanged.** Key names, the now/next/later shape, and the `polled_at` and `boost_ends_at` columns, which are stored as real datetimes.
- **Other writes.** The weather tables use plain float and date columns, no JSON columns, so they cannot have this bug; the audit is recorded in the PR.
- **ADR-0017.** A short dated note records that stored datetimes are ISO-8601 strings.

## Testing Decisions

- Tests use a real-shaped schedule (nested dicts, ints, datetimes) at the existing seams, with the SQLite-backed `mariadb_client` fixture, which reproduces the same `TypeError`. Prior art: `test_heating_retrieval.py`.
- Cases: a real-shaped schedule is written and read back with ISO strings and numeric fields intact; a datetime-free schedule round-trips unchanged; `HeatingRetriever.refresh` end to end persists a real-shaped schedule without error; `polled_at` and `boost_ends_at` remain datetimes; the caller's schedule is not mutated.

## Out of Scope

- Changing the schedule's shape or key names, adding flat columns for it, or changing Grafana queries.
- Deploying the fix or touching the live Pi.
- The "recovered" notification wording, which says polling has resumed when the write could still fail.

## Further Notes

- Found by recovering Hive auth on the live system after PRs #563 and #569 and then reading `job_run`. Auth recovery had been hiding this failure all along.

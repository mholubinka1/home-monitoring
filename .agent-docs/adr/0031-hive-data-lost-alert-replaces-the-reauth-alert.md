# Alert on stale Hive data, not on a re-authentication failure

Status: accepted (supersedes the alert timing and message set in [ADR-0018](0018-ntfy-for-hive-reauth-alerting.md))

hive-app's ntfy alert fired on the first re-authentication failure, yet the 2026-11-02 incident healed unaided after 101 minutes with no SMS login, and the two worst outages (2026-10-20 to 10-28, 7.6 days; 2026-10-29, 4 hours) failed with `'NoneType' object has no attribute 'get'` and raised no alert at all. We now alert on the symptom the owner cares about: the newest `heating_status` row is more than 30 minutes old. A job checks this every minute against MariaDB, so any cause is covered and the clock survives a restart. It sends one "Hive data lost" push (the body names the age and the latest failed `heating_refresh` reason from `job_run`, and links the runbook), repeats hourly while data is missing, and sends one "recovered" notice with the outage length once data returns, only if a lost alert was sent. Nothing is sent between `ntfy.quiet_hours.start` and `ntfy.quiet_hours.end` (default 22:00 to 08:00 local); held messages go out when the window ends, and an outage that starts and heals inside it sends nothing.

The 30 minutes comes from the data: since polling began on 2026-10-03, 15 of 18 gaps over five minutes healed themselves within 19 minutes, and every real outage lasted 101 minutes or more.

## Considered options

- **Count failures inside the poll job** (extend `ReauthAlert`): rejected. A restart resets the count, a hung poll never reports, and every new failure path must remember to report.
- **Keep the re-auth alert alongside the new one**: rejected. It pages immediately for something that heals, then the new alert would page a second time for the same incident.
- **A longer or configurable threshold**: not needed; the observed gaps leave a wide margin and the value is a constant.

## Consequences

- `ReauthAlert`, `NtfyReauthNotifier` and their wiring in the poll and startup auth are removed; `HiveReauthRequired` and `HiveApiUnavailable` stay as ordinary poll errors.
- The alert cannot fire if the hive-app container itself is down; that needs an external heartbeat.
- The in-memory "already alerted" marker resets on restart, so a restart mid-outage may send one extra alert.

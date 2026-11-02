# Alert via ntfy when Hive thermostat data stops arriving

## Problem Statement

hive-app can stop receiving Hive Heating Status without the owner knowing. Since polling began on 2026-10-03 there were three real outages: 7.6 days from 2026-10-20, 4 hours on 2026-10-29, and 101 minutes on 2026-11-02. Only the last raised a notification, because the existing ntfy alert fires solely on a Hive re-authentication failure. The first two failed with a different error and went unnoticed for days. The re-auth alert is also noisy: it pages on the first failure, and the 2026-11-02 incident healed by itself with no SMS login. 15 other gaps over five minutes (5 to 19 minutes each) were harmless Hive API timeouts.

## Solution

The owner gets one ntfy notification, the **Hive Data Lost Notification**, when the newest `heating_status` row is more than 30 minutes old, whatever the cause. It repeats hourly while data is missing, is silent between 22:00 and 08:00 by default, and is followed by one **recovered** notice with the outage length once data returns. The two re-authentication notifications are removed.

## User Stories

1. As the owner, I want an ntfy push when no Hive Heating Status has arrived for 30 minutes, so that a multi-hour or multi-day outage cannot go unnoticed.
2. As the owner, I want transient Hive API gaps of up to 19 minutes to stay silent, so that I am not paged for blips that heal themselves.
3. As the owner, I want the alert to fire whatever the failure (re-authentication, an empty device list, a hung poll, a crashed job), so that I do not depend on the poll code reporting each new failure type.
4. As the owner, I want the alert body to say how long data has been missing and the latest failed-poll reason, so that I can judge urgency without opening Grafana.
5. As the owner, I want the alert to link the re-authentication runbook, so that if the cause is a lost Hive device I can recover.
6. As the owner, I want a reminder every hour while the outage continues, so that a dismissed push does not become a silent multi-day outage.
7. As the owner, I want no notification between 22:00 and 08:00 (configurable), so that I am not woken for an outage.
8. As the owner, I want a held alert or reminder to be sent at the end of the quiet window if data is still missing, so that overnight outages are reported in the morning.
9. As the owner, I want an outage that starts and heals inside the quiet window to send nothing, so that I am not told about problems that no longer exist.
10. As the owner, I want a "recovered" notice with the outage length when data returns after I was alerted, so that I know it is over, and no recovered notice when I was never alerted.
11. As the owner, I want a recovery that happens in the quiet window to be reported at the end of it, so that nothing wakes me for good news.
12. As the owner, I want the alert to keep working across a hive-app restart mid-outage, so that restarting the container does not reset the 30-minute clock.
13. As the owner, I want `ntfy.quiet_hours.start` and `ntfy.quiet_hours.end` in `config.yml`, defaulting to 22:00 and 08:00 in the configured timezone, so that I can change them without a code change.
14. As the owner, I want the old re-authentication-required and authentication-recovered pushes gone, so that one incident produces one kind of alert.

## Implementation Decisions

- A new **Hive Data Lost Notification** concept replaces the old Hive Auth Notification (see the glossary and ADR-0031, which partly supersedes ADR-0018).
- A watchdog job in hive-app runs every 60 seconds on the existing scheduler. It reads the newest `heating_status.polled_at` and the latest failed `heating_refresh` `job_run` error from MariaDB; it does not rely on the poll job reporting to it.
- Rules, all constants: lost after 30 minutes without a row; reminder every 60 minutes after the first alert; recovered notice only after a delivered lost alert. All times compare in UTC; the quiet window is evaluated in the configured local timezone and may span midnight.
- Quiet window behaviour: while inside it nothing is sent; the state is held. When it ends, if data is still missing, the alert (or the next due reminder) is sent; if data returned during it and a lost alert had been delivered, the recovered notice is sent at the end of it; if data returned and no lost alert had been delivered, nothing is sent.
- The "already alerted" marker, last-alert time and outage start are in memory only; the age of the data comes from the database. A restart mid-outage may send one extra alert, accepted. A failed delivery does not set the marker, so it is retried on the next check.
- Message: title `hive-app: Hive data lost`, high priority, tags `warning`, `Click` the runbook; body gives the data age and the latest failed-poll reason (truncated, ASCII-safe for headers where needed). Recovered: title `hive-app: Hive data restored`, default priority, tag `white_check_mark`, body gives the outage length. Same topic and POST mechanism as today's notifier.
- Config: `ntfy.quiet_hours.start` and `ntfy.quiet_hours.end` as `HH:MM` strings, optional, default 22:00 and 08:00; validated at load; `timezone` reused.
- Removed: `ReauthAlert`, `NtfyReauthNotifier`, `ReauthNotifier` and their construction and injection into `HeatingRetriever` and `HiveAuthenticator`. `HiveReauthRequired` and `HiveApiUnavailable` stay.
- No ntfy configured: the watchdog is not registered and a warning is logged at startup, as today.
- No schema changes. `deployments/hive-app/config.yml.template`, `REAUTH_RUNBOOK.md` and the README ntfy text are updated to match.

## Testing Decisions

- Test external behaviour through one seam: the watchdog's check at a given time, run against the real `mariadb_client` test fixture with `responses` standing in for ntfy.sh and the notification titles, priorities and bodies asserted. The time is passed in, so no real clock or sleeping.
- Cases: boundary at 30 minutes; a 19-minute gap is silent; reminder cadence; quiet-window hold, release and silent heal; recovery with and without a prior alert; the outage length and latest failure reason in the body; failed delivery retried; restart mid-outage (a fresh watchdog instance on the same data).
- Config loading tests for the quiet-hours defaults and validation, in the existing `test_hive_config_settings.py` style.
- Wiring: the job is registered on a 60-second interval only when ntfy is configured, and `main` no longer builds a re-auth alert.
- Prior art: `test_reauth_alert_wiring.py`, `test_ntfy_reauth_notifier.py`, `test_hive_refresh_scheduling.py`. The re-auth tests that cover removed behaviour are deleted or rewritten.

## Out of Scope

- Detecting hive-app itself being down (needs an external heartbeat).
- Alerting on the weather jobs, the octopus-app jobs, or any other failure.
- Changing how the poll retries, or fixing the root cause of the `'NoneType' object has no attribute 'get'` failures.
- Configurable threshold or reminder interval.
- A Grafana panel for the gaps.

## Further Notes

- Evidence: scratch analysis scripts in the session scratchpad (`hive_gaps.py`, `hive_outages.py`) over the Pi's `heating_status` and `job_run` tables, 2026-10-03 to 2026-11-02.
- Open implementation check: confirm the timezone in which `heating_status.polled_at` is stored (UTC expected) before comparing it with the current time.

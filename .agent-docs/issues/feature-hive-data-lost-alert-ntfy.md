# Issues: feature-hive-data-lost-alert-ntfy

## Alert when Hive data stops arriving, and say when it returns

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5, 10, 12

### What to build

A watchdog job, registered on the scheduler every 60 seconds whenever ntfy is configured, reads the newest Heating Status time and the latest failed heating poll reason from MariaDB. When the newest row is more than 30 minutes old it sends one high-priority "Hive data lost" ntfy message giving the data age and the failure reason, linking the runbook. When data returns after a delivered alert it sends one "Hive data restored" message with the outage length. State is in memory, so a restart mid-outage rebuilds from the database; a failed delivery is retried on the next check.

### Acceptance criteria

- [ ] Given the newest row is 31 minutes old, when the watchdog checks, then one high-priority lost message is posted with the data age, the latest failed-poll reason and the runbook link
- [ ] Given a 19-minute gap, when the watchdog checks, then nothing is posted
- [ ] Given a lost message was delivered and a new row then arrives, when the watchdog checks, then one restored message with the outage length is posted, and none if no lost message was delivered
- [ ] Given a lost message is already out, when the watchdog checks again with data still missing inside the hour, then nothing more is posted
- [ ] Given a failed delivery, when the watchdog next checks, then it tries again
- [ ] Given a restart mid-outage, when a new watchdog checks, then it alerts from the database age without waiting another 30 minutes
- [ ] The job is registered on a 60-second interval only when ntfy is configured

---

## Remind hourly while the outage lasts

**Blocked by**: #714

**User stories**: 6

### What to build

While data is still missing after a lost message, the watchdog sends a reminder every 60 minutes, with the current data age in the body.

### Acceptance criteria

- [ ] Given a lost message was sent, when 60 minutes pass with data still missing, then one reminder is posted with the updated data age
- [ ] Given 59 minutes have passed, then no reminder is posted
- [ ] Given data returns, then no further reminders are posted

---

## Stay silent in quiet hours

**Blocked by**: #715

**User stories**: 7, 8, 9, 11, 13

### What to build

`ntfy.quiet_hours.start` and `ntfy.quiet_hours.end` (`HH:MM`, default 22:00 and 08:00, evaluated in the configured timezone, may span midnight) are read from `config.yml` and validated. The watchdog sends nothing inside the window and holds its state; when the window ends it sends whatever is due: the lost message or next reminder if data is still missing, or the restored notice if a lost message had been delivered and data has returned.

### Acceptance criteria

- [ ] Given the quiet-hours settings are omitted, then they default to 22:00 and 08:00
- [ ] Given invalid times, then config loading fails with a clear error
- [ ] Given an outage crosses the 30-minute mark at 23:30, then nothing is sent until 08:00, when the lost message is posted if data is still missing
- [ ] Given an outage that starts and heals inside the window, then nothing is ever sent
- [ ] Given a lost message was delivered at 21:30 and data returns at 03:00, then the restored notice is posted at 08:00
- [ ] Given a window that spans midnight and one that does not, then both are honoured

---

## Retire the re-authentication alerts

**Blocked by**: #714

**User stories**: 14

### What to build

Remove `ReauthAlert`, `NtfyReauthNotifier` and their wiring in the heating poll, startup authentication and `main`; keep `HiveReauthRequired` and `HiveApiUnavailable` as ordinary poll errors. Update the config template, runbook and README to describe the data-lost alert and the quiet-hours settings, and delete or rewrite the tests of the removed behaviour.

### Acceptance criteria

- [ ] A re-authentication failure no longer posts any ntfy message by itself
- [ ] No reference to the removed classes remains in code, tests or docs
- [ ] The config template, runbook and README describe the data-lost alert and `ntfy.quiet_hours`
- [ ] The existing poll, retry and startup-auth behaviour is otherwise unchanged and its tests pass

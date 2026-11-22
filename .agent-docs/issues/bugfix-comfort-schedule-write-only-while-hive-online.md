# Issues: bugfix-comfort-schedule-write-only-while-hive-online

## Each heating poll records Hive Online

**GitHub issue**: #825

**Blocked by**: None

**User stories**: 8

### What to build

The heating poll reads the heating product's `props.online` from the same product snapshot as the mode, carries it on `HeatingStatus.online`, and stores it in a new nullable `heating_status.online` column that Schema Sync adds. A missing flag is stored as NULL and counts as online.

### Acceptance criteria

- [x] Given Hive reports the heating product offline, when the heating poll runs, then the stored poll has online false.
- [x] Given Hive's product has no online flag, when the heating poll runs, then the stored poll has online NULL.

---

## Comfort Schedule is confirmed by a later poll, with Away at 10 °C

**GitHub issue**: #826

**Blocked by**: #825

**User stories**: 1, 2, 3, 4, 5

### What to build

The Away Temperature defaults to 10 °C. The Comfort Schedule write posts only while Hive reports the heating product online, with no same-session read-back. A posted week is `sent` and becomes `applied` when a later poll reads that exact week (fingerprints taken of a normalised week, so Hive's whole-number targets match). Until then it is resent at every online poll with no new row, unless the plan changes; after 15 minutes it is recorded once as `not_verified` with a WARNING. Offline, nothing is posted and no row is written. The 60-minute not-verified hold goes. Template, README and spec follow.

### Acceptance criteria

- [x] Given no away_temperature is configured, when the settings load, then the Away Temperature is 10.0.
- [x] Given the built week differs from Hive's and Hive is online, when the heating poll runs, then the week is posted once, with no read-back, and recorded `sent` with its fingerprint.
- [x] Given a `sent` week, when a later poll reads that week from Hive with whole-number targets, then `applied` is recorded.
- [x] Given a `sent` week not yet shown by Hive, when the next online poll runs, then the week is resent and no new row is written.
- [x] Given a `sent` week still not shown 15 minutes after it was sent, when the next online poll runs, then `not_verified` is recorded once with a WARNING and the week is resent.
- [x] Given a `sent` week, when the plan changes, then the new week is posted at the next online poll and recorded `sent`.
- [x] Given Hive reports the heating product offline at the poll, when the built week differs, then nothing is posted and no row is written.
- [x] Given the product is offline in the write's own session, when the week is written, then nothing is posted and no row is written.

---

## Presence writes are skipped while Hive is offline

**GitHub issue**: #827

**Blocked by**: #825

**User stories**: 6

### What to build

A real Away Override or restore is skipped while the poll reports the heating product offline, with a DEBUG log and no row; the next online poll tries again. Dry runs, adoption and the OFF/boost rules are unchanged.

### Acceptance criteria

- [x] Given No One Is Home and Hive reports the heating product offline, when the heating poll runs, then no Away Override is sent and no row is written.
- [x] Given that skipped Away Override, when a later poll finds Hive online, then the Away Override is sent.

---

## Heating Unreachable alert

**GitHub issue**: #828

**Blocked by**: #825

**User stories**: 7

### What to build

A new watchdog, built when ntfy is configured and `comfort_schedule.control` is `on`, reads stored polls: offline or mode OFF continuously for over 30 minutes sends one ntfy alert naming the reason, and the next reachable poll sends one "reachable again" notice with the spell's length. No reminders; quiet hours respected; state in memory.

### Acceptance criteria

- [x] Given stored polls offline or OFF for 31 minutes and control `on`, when the watchdog checks, then one alert is sent naming the reason.
- [x] Given stored polls unreachable for 29 minutes, when the watchdog checks, then no alert is sent.
- [x] Given an alert was sent and polls are still unreachable, when the watchdog checks again, then no second alert is sent.
- [x] Given an alert was sent, when a reachable poll is stored and the watchdog checks, then one "reachable again" notice is sent with the spell's length.
- [x] Given an unreachable spell over 30 minutes during quiet hours, when the watchdog checks, then no alert is sent.

---

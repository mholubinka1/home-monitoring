# Comfort Schedule: write only while Hive is online, confirm by a later poll

## Problem Statement

On the live account (2026-11-21) every Comfort Schedule write was recorded as `applied`,
but almost none reached the thermostat:

- Hive's heating product and thermostat reported `props.online` false for most of the day,
  with short online bursts. A write while offline got either HTTP 200 with the week echoed
  back, or HTTP 404 `NOT_FOUND`. Neither reached the thermostat.
- The `applied` check reads the product back in the write's own session. That read only
  echoes what was just posted, so it said `applied` for writes that never landed. The
  same happened to a restore to SCHEDULE.
- Each heating poll still saw the old week, so the built week "differed" and hive-app
  rewrote the identical week every 2 minutes.
- The Away Temperature defaults to 7 °C, which is Hive's frost protection. The Hive app
  shows it as "Off", so the household can't tell Away from Off.
- When Hive is offline or in mode OFF, the Comfort Schedule can't take effect, and
  nobody is told.

pyhive-integration 2.0.0b5 posts the same body to the same endpoint, so the write format
is not the fault. It also skips a write while the product is offline.

## Solution

hive-app writes the Comfort Schedule only while Hive reports the heating product online,
and trusts only a later regular poll to say a write landed. The flow:

1. A posted week is recorded as `sent`.
2. It becomes `applied` when a later poll reads that exact week.
3. Until then it's resent at every poll while Hive is online (every 2 minutes), with no
   new row, unless the plan changes, in which case the new week is posted instead.
4. After 15 minutes without landing, it's recorded once as `not_verified` with a WARNING,
   and resending continues.

Presence writes (the Away Override and the restore) are skipped while Hive is offline.
When Hive has been offline or OFF for 30 minutes and the Comfort Schedule is `on`, one
ntfy alert goes out, then one notice when it clears. The Away Temperature defaults to
10 °C.

## User Stories

1. As the householder, I want the Away Temperature to default to 10 °C, so that Away is
   visible in the Hive app and never mistaken for Off.
2. As the householder, I want hive-app to post the Comfort Schedule only while Hive reports
   the heating product online, so that writes Hive can't deliver aren't sent or recorded as
   done.
3. As the householder, I want a write counted as applied only when a later poll reads
   that exact week from Hive, so that the record shows what the thermostat really holds.
4. As the householder, I want a week that hasn't shown up resent at every poll while Hive
   is online, until a poll reads it, so that a short online moment of the thermostat is
   used, and so that resending stops once the week has landed.
5. As the householder, I want a week that still hasn't shown up after 15 minutes recorded
   once as not verified, with a WARNING, so that a write Hive keeps ignoring is visible
   without one row per poll.
6. As the householder, I want presence writes skipped while Hive is offline, so that an
   Away Override or restore is sent only when Hive can deliver it.
7. As the householder, I want one ntfy alert when Hive has been offline or OFF for 30
   minutes while the Comfort Schedule is on, and one notice when it clears, so that I
   know when the schedule can't take effect.
8. As the householder, I want each poll to store whether Hive reported the heating
   product online, so that offline spells can be seen afterwards.

## Implementation Decisions

- **Away Temperature** (`hive_app.common.config`, `ComfortScheduleSettings`): the
  default becomes 10.0. The 5–32 range and the half-degree rule are unchanged. The
  config template, README and glossary say why 7 °C is a poor choice: the Hive app shows
  it as "Off".
- **Hive Online** (`hive_app.data.model.HeatingStatus`, `hive_app.data.hive_client`): the
  poll reads the heating product's `props.online` from the same product snapshot as the
  mode, into a new `HeatingStatus.online: bool | None` (default None). A missing or
  non-boolean flag is None, and None counts as online, so an account without the flag
  behaves as today.
- **Stored flag** (`hive_app.data.mysql.model.heating_status`): a new nullable Boolean
  `online` column, written with each poll. Schema Sync adds it to the existing table at
  startup. Rows before the change hold NULL.
- **POST only** (`HiveApiSource.write_comfort_schedule`): it still validates the week
  first. It then opens its own session, checks the product's `online` flag in that
  session's snapshot, and posts the week only when the flag isn't false. A posted week
  returns `sent`. A product offline in that snapshot returns `offline` without posting:
  this covers a flap between the poll and the write, and the 404s seen while offline.
  The same-session read-back and its re-read (`SCHEDULE_REREAD_SECONDS`) are deleted. A
  non-2xx answer while online still raises, and is stored as `failed` as today.
- **Comfort Schedule state** (`hive_app.data.heating.ComfortScheduleControl`, control
  `on`): the newest real `comfort_schedule` action is the state. A `sent`, `applied` or
  `not_verified` row carries the week's fingerprint in `detail`. Each poll, after the
  existing "no readable week" check:
  1. If the newest row is pending (`sent` or `not_verified`) and Hive's week has that
     row's fingerprint, record `applied` with the same fingerprint.
  2. If the built week equals Hive's week, stop.
  3. If Hive reports the product offline, stop. No row is written, and the next online
     poll tries again.
  4. If the newest row is pending with the built week's fingerprint, resend it with no new
     row (DEBUG log). If that row is `sent` and was written 15 minutes ago or more
     (`NOT_VERIFIED_AFTER`), also record `not_verified` (same fingerprint) with a
     WARNING. That happens once, because the newest row is then `not_verified`.
  5. Otherwise (no pending week, or the plan changed), post now and record `sent`.

  A post or resend that returns `offline` writes no row. One that raises is recorded as
  `failed`, as today, and the next online poll posts afresh. The 60-minute
  `NOT_VERIFIED_BACKOFF` and its reader go. A new MariaDB reader returns the newest real
  comfort_schedule action (`acted_at`, `outcome`, `detail`). Dry run is unchanged.
- **Fingerprint** (`hive_app.data.comfort_schedule`): taken of a normalised week: each
  day's entries sorted by start, and every target as a float. Hive returns 17 where 17.0
  was sent, so a raw fingerprint would never match. Dry run uses the same function, so
  one extra dry-run record may follow the deploy.
- **Presence gate** (`PresenceHeatingControl`): a real write (Away Override or restore)
  is skipped while `status.online` is False, with a DEBUG log and no row. The next poll
  tries again. Dry-run records, adopting a hand-set MANUAL, and the OFF/boost rules are
  unchanged.
- **Heating Unreachable alert** (new `hive_app.data.heating_watchdog`, on the
  `PresenceWatchdog` pattern):
  - When it runs: a job every `PRESENCE_WATCHDOG_INTERVAL_SECONDS`, registered only when
    ntfy is configured and `comfort_schedule.control` is `on`.
  - Unreachable means a stored poll with `online` false or mode `OFF`. A poll with no
    mode (NULL) and `online` not false is reachable.
  - Alert: the newest stored poll is unreachable and has been since a time more than
    `LOST_AFTER` (30 minutes) ago. The start is the first unreachable poll after the
    newest reachable one, or the oldest poll read if there is none. Only the last 7 days
    of polls are read (`SPELL_WINDOW`, on the `polled_at` index), so the per-minute check
    never scans the whole table on the Pi's 512 MB MariaDB. A longer spell is still one
    spell: while an alert is outstanding, a new alert needs a reachable poll after the
    alerted start. One alert per spell, no reminders, with its reason (offline, OFF, or
    both). A send that fails is not counted as delivered and is retried at the next
    check, as the presence watchdog does.
  - Clear: one "reachable again" notice with the spell's length once a reachable poll
    follows an alert.
  - State is in memory, so a restart mid-spell alerts again. Quiet hours are respected
    as the presence watchdog does.
  - A MariaDB reader returns the start of the current unreachable spell, or None.
    `NtfyDataNotifier` gains `notify_heating_unreachable(age, reason)` and
    `notify_heating_reachable(outage)`.
- **No octopus-app or dashboard change.** Nothing reads the action outcomes, and the new
  column is additive.
- **Feature spec**: `feature-presence-autonomous-heating` ("Write path" read-back,
  "When" and its `not_verified` hold, the outcome list, and the Away Temperature default)
  is amended to match. README's Comfort Schedule and presence sections follow.

## Testing Decisions

- Test behaviour through the existing seams, with no unit tests of private helpers:
  - **`HeatingRetriever.refresh(now)`** with the fake HiveSource and the real MariaDB
    test client (`test_comfort_schedule_write.py`, `test_heating_control.py`):
    - `sent` on a post;
    - `applied` when a later poll reads the sent week, including Hive's whole-number
      targets;
    - a resend with no new row at the next online poll;
    - one `not_verified` with a WARNING at 15 minutes, and resending continuing;
    - an immediate post when the plan changes;
    - no post and no row while offline, and no row on an `offline` return;
    - a presence write skipped while offline;
    - the online flag stored with the poll.
  - **`HiveApiSource`** with the fake Hive (`test_comfort_schedule_hive_write.py`,
    heating retrieval tests): the poll reads `props.online`; a write posts once with no
    read-back and returns `sent`; an offline product returns `offline` without posting; a
    refusal still raises.
  - **The heating watchdog's `check(now)`** with a fake notifier and `responses`, as
    `test_presence_watchdog.py` does: no alert under 30 minutes; one alert after 30 for
    offline, OFF or both; no second alert; one reachable notice; quiet hours.
  - **Config**: the default Away Temperature is 10.0.
- Tests use the made-up 05:40–06:25 slot at 19 °C. The repo is public.
- Existing tests that assume the read-back, the 60-minute hold or `applied` on a post are
  updated to the new rules.

## Out of Scope

- Changing Hive's mode out of OFF.
- Confirming presence writes by a later poll.
- Fixing the thermostat's link to the hub (hardware or Hive support).
- Any change to the write's body or endpoint (it matches the library).
- Turning `comfort_schedule.control` back to `on`: it stays `dry_run` on the Pi until
  Hive reports the thermostat online reliably.

## Further Notes

- Live evidence and the 2.0.0b5 comparison come from spikes on 2026-11-21. That day, the
  household's own Hive app edits landed and hive-app's did not, even during an
  8-minute online burst. Whether writes land once the thermostat link is fixed is still
  unproven, and the poll check will show it.
- Deploy: hive-app only. Set `comfort_schedule.away_temperature` to 10.0 on the Pi (or
  rely on the new default). Keep `presence` and `comfort_schedule` on `dry_run`. Change
  the two controls only together: validation refuses presence `on` unless the Comfort
  Schedule is `on`.

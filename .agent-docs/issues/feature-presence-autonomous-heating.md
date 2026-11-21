> Work complete — PR ready to merge.

# Issues: feature-presence-autonomous-heating

## Record presence from Pi-hole phones

**GitHub issue**: #801

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 16

### What to build

hive-app gains an optional `presence` config section (Pi-hole URL and app password, both refused blank; Away Delay; `phone_hostnames`; `ignored_hostnames`; `control` defaulting to `off`) and a presence job every 60 seconds, scheduled like the Hive data watchdog (catch and log, no backoff). The job reads Pi-hole's device list through a new PiholeSource (Pi-hole v6 API: session login reused across polls, re-login on 401, logout at shutdown; `network/devices` always sent with explicit `max_devices` and `max_addresses`). It calls each device a Phone by the Phone Rule, ported from [the prototype note](../research/phone-rule-prototype.md): config ignored and extra hostnames first, then the randomised-MAC gate (a non-MAC such as FTL's `ip-<address>` fails it without raising), then hostname words. It decides Someone Is Home, No One Is Home or Presence Unknown. Each change of state closes the open row and opens a new one in the new `presence_period` table. The config template gains the section with blanks. This slice makes no Hive writes.

### Acceptance criteria

- [x] Given a device with a randomised MAC and hostname `Alex-s-Phone` that made a lookup 5 minutes ago, when the presence job runs, then a `someone_home` Presence Period is open with `phones_seen` 1.
- [x] Given only devices with factory MACs active, when the presence job runs, then a `no_one_home` period is open.
- [x] Given a randomised-MAC device named `Galaxy-Tab-S9` active 1 minute ago and no other Phone, when the presence job runs, then the house is No One Is Home.
- [x] Given the only Phone's last lookup was exactly 30:00 ago, when the presence job runs, then Someone Is Home; and given it was 30:01 ago, then No One Is Home.
- [x] Given a device whose hardware address is `ip-192.0.2.5` and hostname `Pixel-8`, when the presence job runs, then it is not a Phone and nothing raises; and given `Pixel-8` is in `phone_hostnames`, then it is a Phone.
- [x] Given `Kevin` (factory MAC) is in `phone_hostnames` and active, when the presence job runs, then Someone Is Home.
- [x] Given `Pixel-8` (randomised MAC) is in `ignored_hostnames` and is the only active device, when the presence job runs, then No One Is Home.
- [x] Given an open `someone_home` period, when No One Is Home is decided, then that period gets an `ended_at` and a `no_one_home` period opens at the same instant.
- [x] Given an open `someone_home` period, when the job decides Someone Is Home again (including after a restart), then no row changes.
- [x] Given Pi-hole is unreachable or rejects the password, when the presence job runs, then an `unknown` period is open, the job does not raise and the password appears in no log line.
- [x] Given no `presence` section in config, when hive-app starts, then no presence job is scheduled.
- [x] Given a `presence` section with a blank Pi-hole URL or password, when hive-app starts, then startup is refused.
- [x] Given the Phone Rule table cases (every listed brand, tablets, watches, computers, Samsung codes, legacy Android, blank names, multicast MACs), when each is classified, then each gets its expected verdict, the same every time.

---

## Alert on a lost presence signal and warn at 03:05

**GitHub issue**: #802

**Blocked by**: #801

**User stories**: 14, 15

### What to build

A presence-signal watchdog in the shape of the Hive data-lost watchdog, driven by the open Presence Period: an ntfy "presence signal lost" message after 30 minutes of Presence Unknown, hourly reminders, and a "restored" message when it ends, all respecting quiet hours. Like the Hive data watchdog, it isn't built without ntfy, and startup warns. A daily job at 03:05 in the configured timezone logs a WARNING when the open period is No One Is Home.

### Acceptance criteria

- [x] Given Presence Unknown has lasted 31 minutes outside quiet hours, when the watchdog checks, then one "presence signal lost" ntfy message is sent.
- [x] Given the signal is still lost an hour after the alert, when the watchdog checks, then one reminder is sent.
- [x] Given an alert was sent, when presence becomes known again, then a "presence signal restored" message is sent.
- [x] Given the signal is lost during quiet hours, when the watchdog checks, then nothing is sent until quiet hours end.
- [x] Given the open period is No One Is Home at 03:05 London time, when the night check runs, then a WARNING with the period's duration is logged.
- [x] Given Someone Is Home at 03:05, when the night check runs, then no warning is logged.

---

## Hold the Away Override while No One Is Home

**GitHub issue**: #803

**Blocked by**: #801

**User stories**: 12, 13, 17, 18, 18a, 19, 20, 21, 22, 23, 24, 24a, 25, 26

### What to build

The HiveSource Protocol gains "apply away override" (one low-level `api.setState` with MANUAL and the Away Temperature) and "restore schedule" (`api.setState` with SCHEDULE). Each opens its own Hive session like every existing verb, re-reads the product afterwards to verify, and turns every library exception into a failure. After storing the Heating Status, HeatingRetriever applies the control policy table from the spec on Presence for control (Presence Unknown keeps the earlier state for 10 minutes, then counts as home):

- re-assert MANUAL at the Away Temperature at every poll while No One Is Home, unless a boost is running or the thermostat is OFF;
- restore SCHEDULE when Someone Is Home follows an override in force, waiting for any boost to end, and skipping (recorded `skipped`) when the thermostat is OFF.

Every intended write is stored in the new `heating_control_action` table with its dry-run flag and outcome. `control: dry_run` records and logs without calling Hive, and treats a dry-run override in force as already applied, so an absence records one override and one restore. Whenever control isn't `on`, a real override still in force is restored once. Failed writes are logged, stored and retried next poll, never raised. Config gains `comfort_schedule.away_temperature` (default 7.0, 5–32 °C on a half degree), and `presence.control: on` is refused at startup until the Comfort Schedule exists (#808 relaxes it). The heating guide describes the behaviour, including the accepted overnight false away.

### Acceptance criteria

- [x] Given `control: on`, No One Is Home and the thermostat in SCHEDULE, when the heating poll runs, then one write sets MANUAL at the Away Temperature and an `away_override` action is stored as `applied`.
- [x] Given the override is in force and someone set MANUAL 21 °C by hand, when the heating poll runs, then MANUAL at the Away Temperature is written again.
- [x] Given No One Is Home and a boost running, when the heating poll runs, then nothing is written.
- [x] Given No One Is Home and the thermostat already at MANUAL 7 °C, when the heating poll runs, then nothing is written.
- [x] Given No One Is Home and the thermostat OFF, when the heating poll runs, then nothing is written.
- [x] Given the override is in force, when the poll sees Someone Is Home, then SCHEDULE is written and a `restore_schedule` action is stored.
- [x] Given the override is in force and Presence Unknown has been open for 9 minutes after No One Is Home, when the heating poll runs, then nothing is written; and at 10 minutes, SCHEDULE is restored.
- [x] Given the override is in force and a boost is running, when the poll sees Someone Is Home, then nothing is written; and once the boost has ended and the thermostat is back at MANUAL 7 °C, SCHEDULE is restored.
- [x] Given the override is in force and the thermostat was switched OFF, when the poll sees Someone Is Home, then nothing is written, a `restore_schedule` action is stored as `skipped`, and later polls write nothing.
- [x] Given the household set MANUAL 21 °C while home and no override is in force, when the poll sees Someone Is Home, then nothing is written.
- [x] Given a restore fails, when the next poll runs, then the restore is attempted again.
- [x] Given a write raises inside the library, when the heating poll runs, then a `failed` action is stored with the exception type as its detail, a WARNING is logged and the poll does not raise.
- [x] Given `control: dry_run` and a whole absence of many polls, when the heating poll runs throughout, then exactly one `away_override` and one `restore_schedule` are stored as `dry_run`, "would set" is logged and Hive gets no call.
- [x] Given `control: off`, when the heating poll runs, then no actions are stored and nothing is written.
- [x] Given a real override is in force and hive-app restarts with `control: dry_run`, `off` or no `presence` section, when the heating poll runs, then SCHEDULE is restored once (retried if it fails) and later polls write nothing.
- [x] Given `presence.control: on` in config, when hive-app starts before the Comfort Schedule exists, then startup is refused.
- [x] Given a boost is started while No One Is Home, when Comfort Days are computed, then its Boost Start counts as evidence and presence stays No One Is Home.

---

## Leave empty-house minutes out of Comfort Day

**GitHub issue**: #804

**Blocked by**: #801

**User stories**: 27

### What to build

octopus-app declares and reads `presence_period`. ComfortDayJob stops counting below-comfort minutes that fall inside a `no_one_home` period. Settled days are never restated.

### Acceptance criteria

- [x] Given the room below Comfort Temperature in Counted Hours, partly inside a `no_one_home` period, when the Comfort Day is settled, then only the minutes outside that period count.
- [x] Given a Comfort Day settled before any presence rows existed, when the job runs again, then that day is unchanged.

---

## Forecast the held target in MANUAL mode

**GitHub issue**: #805

**Blocked by**: None

**User stories**: 28

### What to build

Whenever the newest heating poll is MANUAL with no boost running, RoomForecastJob's current scenario holds that poll's stored target for the whole horizon instead of following the Hive schedule (today only OFF is handled). This covers the Away Override once it is actually applied, and a hand-set MANUAL. `read_latest_heating_status` also returns the target and boost flag. No `presence_period` read. The plan scenario is unchanged.

### Acceptance criteria

- [x] Given the newest poll is MANUAL at 7 °C with no boost, when the Room Forecast runs, then the current scenario holds 7 °C for the whole horizon.
- [x] Given the newest poll is MANUAL at 21 °C set by hand, when the Room Forecast runs, then the current scenario holds 21 °C.
- [x] Given the newest poll is SCHEDULE, or MANUAL with a boost running, when the Room Forecast runs, then the current scenario follows the Hive schedule as before.

---

## Presence panel on the gas and heating dashboard

**GitHub issue**: #806

**Blocked by**: #801

**User stories**: 29

### What to build

A state-timeline panel showing the last 7 days of Presence Periods in London time, with its SQL in the gas-heating queries document and covered by the dashboard and queries-document tests. The SQL is measured on a throwaway copy of the database, never on the live MariaDB.

### Acceptance criteria

- [x] Given Presence Periods over the last 7 days, when the dashboard loads, then a state timeline shows each state in London time.
- [x] Given the panel's SQL, when it is measured on a throwaway copy of the database, then that copy's peak memory stays under the live server's 512 MB cap, and the SQL is documented in the queries document.

---

## Spike: prove the Hive schedule write

**GitHub issue**: #807

**Blocked by**: None

**User stories**: 36

### What to build

A one-off live check, runnable at any time, that posts `{"schedule": {...}}` for all seven days to the heating node through pyhive-integration 1.0.10's authenticated request, with one weekday changed to a made-up slot. It reads the result back, restores the original week and records the findings in a research note. The note also records the 2.0 beta's `set_schedule` validation rules from its source at a named version. No production code.

### Acceptance criteria

- [x] Given the live account, when a made-up schedule (05:40–06:25 at 19 °C) is posted to one weekday and read back, then the research note records acceptance, the start-time grain, the slot limit and any 200-but-unstored behaviour, and the original week is restored.
- [x] Given the 2.0 beta's source at a named version, when the spike is written up, then the note lists its `set_schedule` validation rules and whether the live account agreed with them.

---

## Write the Comfort Schedule from the Comfort Plan

**GitHub issue**: #808

**Blocked by**: #807; #803

**User stories**: 24, 30, 31, 32, 33, 34, 35, 35a

### What to build

A `comfort_schedule.control` setting (`off`, `dry_run` or `on`, default off), working with or without a `presence` section; `presence.control: on` is now accepted only when it is `on`. `pyhive-integration` is pinned to `>=1.0.10,<2`. At each heating poll, hive-app builds the week from the Weekly Schedule it just read: today's and tomorrow's weekdays are replaced from the newest `plan` slots in `room_forecast_slot`, with starts rounded earlier and ends rounded later to the grain the spike proved. Plan instants are converted at their own London offset. If the result differs from Hive's, it posts the full week in its own Hive session, validates and reads it back, and records a `comfort_schedule` action. A failed write is retried next poll; after `not_verified` there's no rewrite for 60 minutes. On the poll that restores SCHEDULE after an override, the schedule is written first. The heating guide describes the Comfort Schedule.

### Acceptance criteria

- [x] Given `comfort_schedule: on` and plan slots for today and tomorrow, when the heating poll runs and the built week differs from Hive's, then all seven days are posted with today and tomorrow replaced (Away Temperature at 00:00, Comfort Temperature from the rounded-earlier start, Away Temperature from the rounded-later end). *Superseded by #823: no Away entry is written at 00:00.*
- [x] Given the built week equals Hive's, when the heating poll runs, then nothing is written.
- [x] Given a plan slot ending at midnight, when the week is built, then that day has no closing entry. *Superseded by #823: it closes with the Away Temperature at 23:59.*
- [x] Given a day with no plan slot, when the week is built, then that day's entries are unchanged.
- [x] Given a plan slot starting at local midnight, when the week is built, then it replaces the leading Away entry, with no second entry at minute 0. *Superseded by #823: there is no leading Away entry; the slot is the day's first entry.*
- [x] Given a plan slot that does not lie within its local day (it starts before the day's midnight or ends after the next one), when the heating poll writes or records the week, then that day keeps Hive's entries and a WARNING names the weekday.
- [x] Given the override is in force and Someone Is Home begins, when the heating poll runs, then the Comfort Schedule is written before SCHEDULE is restored.
- [x] Given Hive answers 200 but the read-back differs, when the write completes, then a `not_verified` action is stored, a WARNING is logged, and the polls of the next 60 minutes write nothing.
- [x] Given `comfort_schedule: dry_run`, when the week differs, then a `dry_run` action is stored and Hive gets no call.
- [x] Given `comfort_schedule: on` and no `presence` section, when the heating poll runs and the week differs, then the Comfort Schedule is written.
- [x] Given `presence.control: on` and `comfort_schedule.control: on`, when hive-app starts, then startup succeeds; and given `comfort_schedule.control: dry_run`, then startup is refused.
- [x] Given a plan slot on a clock-change day, when the week is built, then its London clock minutes are right for that day's offset.

---

## Move to pyhive-integration 2.0's set_schedule once stable

**GitHub issue**: #809

**Blocked by**: #808

**User stories**: 34

### What to build

Once pyhive-integration 2.0 is released stable, port hive-app's login and resume to its renamed API and replace the own-POST schedule write with the library's `set_schedule`, keeping the read-back verification.

### Acceptance criteria

- [ ] Given pyhive-integration 2.0 is released stable, when hive-app is upgraded, then the Comfort Schedule is written through `set_schedule`, and the login, resume and Comfort Schedule tests all pass.

---

# Issues: feature-presence-autonomous-heating

## Record presence from Pi-hole phones

**GitHub issue**: #801

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 16

### What to build

hive-app gains an optional `presence` config section (Pi-hole URL and app password, Away Delay, `phone_hostnames`, `ignored_hostnames`, `control` defaulting to `off`) and a presence job every 60 seconds. The job reads Pi-hole's device list through a new PiholeSource (Pi-hole v6 API: session login reused across polls, re-login on 401, logout at shutdown), calls each device a Phone by the Phone Rule (randomised MAC gate, then hostname words; config extra and ignored hostnames take precedence), and decides Someone Is Home, No One Is Home or Presence Unknown. Each change of state closes the open row and opens a new one in the new `presence_period` table. The config template gains the section with blanks. This slice makes no Hive writes.

### Acceptance criteria

- [ ] Given a device with a randomised MAC and hostname `Alex-s-Phone` that made a lookup 5 minutes ago, when the presence job runs, then a `someone_home` Presence Period is open with `phones_seen` 1.
- [ ] Given only devices with factory MACs active, when the presence job runs, then a `no_one_home` period is open.
- [ ] Given a randomised-MAC device named `Galaxy-Tab-S9` active 1 minute ago and no other Phone, when the presence job runs, then the house is No One Is Home.
- [ ] Given the only Phone's last lookup was 29 minutes ago, when the presence job runs, then Someone Is Home; and given it was 31 minutes ago, then No One Is Home.
- [ ] Given `Kevin` (factory MAC) is in `phone_hostnames` and active, when the presence job runs, then Someone Is Home.
- [ ] Given `Pixel-8` (randomised MAC) is in `ignored_hostnames` and is the only active device, when the presence job runs, then No One Is Home.
- [ ] Given an open `someone_home` period, when No One Is Home is decided, then that period gets an `ended_at` and a `no_one_home` period opens at the same instant.
- [ ] Given an open `someone_home` period, when the job decides Someone Is Home again (including after a restart), then no row changes.
- [ ] Given Pi-hole is unreachable or rejects the password, when the presence job runs, then a `presence_unknown` period is open and the password appears in no log line.
- [ ] Given no `presence` section in config, when hive-app starts, then no presence job is scheduled.
- [ ] Given the Phone Rule table cases (every listed brand, tablets, watches, computers, Samsung codes, legacy Android, blank names, multicast MACs), when each is classified, then each gets its expected verdict, the same every time.

---

## Alert on a lost presence signal and warn at 03:05

**GitHub issue**: #802

**Blocked by**: #801

**User stories**: 14, 15

### What to build

A presence-signal watchdog in the shape of the Hive data-lost watchdog, driven by the open Presence Period: an ntfy "presence signal lost" message after 30 minutes of Presence Unknown, hourly reminders, and a "restored" message when it ends, all respecting quiet hours (log only without ntfy). A daily job at 03:05 in the configured timezone logs a WARNING when the open period is No One Is Home.

### Acceptance criteria

- [ ] Given Presence Unknown has lasted 31 minutes outside quiet hours, when the watchdog checks, then one "presence signal lost" ntfy message is sent.
- [ ] Given the signal is still lost an hour after the alert, when the watchdog checks, then one reminder is sent.
- [ ] Given an alert was sent, when presence becomes known again, then a "presence signal restored" message is sent.
- [ ] Given the signal is lost during quiet hours, when the watchdog checks, then nothing is sent until quiet hours end.
- [ ] Given the open period is No One Is Home at 03:05 London time, when the night check runs, then a WARNING with the period's duration is logged.
- [ ] Given Someone Is Home at 03:05, when the night check runs, then no warning is logged.

---

## Hold the Away Override while No One Is Home

**GitHub issue**: #803

**Blocked by**: #801

**User stories**: 12, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26

### What to build

The HiveSource Protocol gains "apply away override" (one `setState` with MANUAL and the Away Temperature) and "restore schedule" (`setMode` SCHEDULE). After fetching the Heating Status, HeatingRetriever applies the control policy from the spec: re-assert MANUAL at the Away Temperature at every poll while No One Is Home unless a boost is running, and restore SCHEDULE when Someone Is Home or Presence Unknown follows an override in force. Every intended write is stored in the new `heating_control_action` table with its dry-run flag and outcome. `control: dry_run` records and logs without calling Hive. Failed writes are logged, stored and retried next poll, never raised. The heating guide describes the behaviour.

### Acceptance criteria

- [ ] Given `control: on`, No One Is Home and the thermostat in SCHEDULE, when the heating poll runs, then one write sets MANUAL at the Away Temperature and an `away_override` action is stored as `applied`.
- [ ] Given the override is in force and someone set MANUAL 21 °C by hand, when the heating poll runs, then MANUAL at the Away Temperature is written again.
- [ ] Given No One Is Home and a boost running, when the heating poll runs, then nothing is written.
- [ ] Given No One Is Home and the thermostat already at MANUAL 7 °C, when the heating poll runs, then nothing is written.
- [ ] Given the override is in force, when the poll sees Someone Is Home or Presence Unknown, then SCHEDULE is written and a `restore_schedule` action is stored.
- [ ] Given the household set MANUAL 21 °C while home and no override is in force, when the poll sees Someone Is Home, then nothing is written.
- [ ] Given a restore fails, when the next poll runs, then the restore is attempted again.
- [ ] Given a write fails, when the heating poll runs, then a `failed` action is stored, a WARNING is logged and the poll does not raise.
- [ ] Given `control: dry_run`, when No One Is Home, then a `dry_run` action is stored, "would set" is logged and Hive gets no call.
- [ ] Given `control: off`, when the heating poll runs, then no actions are stored and nothing is written.
- [ ] Given a boost is started while No One Is Home, when Comfort Days are computed, then its Boost Start counts as evidence and presence stays No One Is Home.

---

## Leave empty-house minutes out of Comfort Day

**GitHub issue**: #804

**Blocked by**: #801

**User stories**: 27

### What to build

octopus-app declares and reads `presence_period`. ComfortDayJob stops counting below-comfort minutes that fall inside a `no_one_home` period. Settled days are never restated.

### Acceptance criteria

- [ ] Given the room below Comfort Temperature in Counted Hours, partly inside a `no_one_home` period, when the Comfort Day is settled, then only the minutes outside that period count.
- [ ] Given a Comfort Day settled before any presence rows existed, when the job runs again, then that day is unchanged.

---

## Forecast the actual target while away

**GitHub issue**: #805

**Blocked by**: #801

**User stories**: 28

### What to build

While the open Presence Period is `no_one_home`, RoomForecastJob's current scenario holds the thermostat's latest stored target for the whole horizon instead of the Hive schedule. The plan scenario is unchanged.

### Acceptance criteria

- [ ] Given an open `no_one_home` period and a latest stored target of 7 °C, when the Room Forecast runs, then the current scenario holds 7 °C for the whole horizon.
- [ ] Given Someone Is Home, when the Room Forecast runs, then the current scenario follows the Hive schedule as before.

---

## Presence panel on the gas and heating dashboard

**GitHub issue**: #806

**Blocked by**: #801

**User stories**: 29

### What to build

A state-timeline panel showing the last 7 days of Presence Periods in London time, with its SQL in the gas-heating queries document and covered by the dashboard and queries-document tests. The SQL is measured on a throwaway copy of the database, never on the live MariaDB.

### Acceptance criteria

- [ ] Given Presence Periods over the last 7 days, when the dashboard loads, then a state timeline shows each state in London time.
- [ ] Given the panel's SQL, when it is measured on a throwaway copy of the database, then it stays within the Pi's memory budget, and it is documented in the queries document.

---

## Spike: prove the Hive schedule write

**GitHub issue**: #807

**Blocked by**: #803

**User stories**: 36

### What to build

A one-off live check, run after part B is live, that posts `{"schedule": {...}}` for all seven days to the heating node through pyhive-integration 1.0.10's authenticated request, with one weekday changed to a made-up slot. It reads the result back, restores the original week and records the findings in a research note. No production code.

### Acceptance criteria

- [ ] Given the live account, when a made-up schedule (05:40–06:25 at 19 °C) is posted to one weekday and read back, then the research note records acceptance, the start-time grain, the slot limit and any 200-but-unstored behaviour, and the original week is restored.

---

## Write the Comfort Schedule from the Comfort Plan

**GitHub issue**: #808

**Blocked by**: #807; #803

**User stories**: 30, 31, 32, 33, 34, 35

### What to build

A `comfort_schedule.control` setting (`off`, `dry_run` or `on`, default off). At each heating poll, hive-app builds the week from the Weekly Schedule it just read: today's and tomorrow's weekdays are replaced from the newest `plan` slots in `room_forecast_slot`, with starts rounded earlier and ends rounded later to the grain the spike proved. If the result differs from Hive's, it posts the full week, validates and reads it back, and records a `comfort_schedule` action. On the poll that restores SCHEDULE after an override, the schedule is written first. The heating guide describes the Comfort Schedule.

### Acceptance criteria

- [ ] Given `comfort_schedule: on` and plan slots for today and tomorrow, when the heating poll runs and the built week differs from Hive's, then all seven days are posted with today and tomorrow replaced (Away Temperature at 00:00, Comfort Temperature from the rounded-earlier start, Away Temperature from the rounded-later end).
- [ ] Given the built week equals Hive's, when the heating poll runs, then nothing is written.
- [ ] Given a plan slot ending at midnight, when the week is built, then that day has no closing entry.
- [ ] Given a day with no plan slot, when the week is built, then that day's entries are unchanged.
- [ ] Given the override is in force and Someone Is Home begins, when the heating poll runs, then the Comfort Schedule is written before SCHEDULE is restored.
- [ ] Given Hive answers 200 but the read-back differs, when the write completes, then a `not_verified` action is stored.
- [ ] Given `comfort_schedule: dry_run`, when the week differs, then a `dry_run` action is stored and Hive gets no call.

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

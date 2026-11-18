# Presence-Based Heating Control and the Comfort Schedule

## Problem Statement

The heating follows a hand-set Hive schedule whether or not anyone is in. When the household goes out for the day or away for a weekend, the boiler still heats an empty house to the full schedule, and the only way to stop it is to remember to change the thermostat. The Comfort Plan already works out the least heating that keeps the room comfortable, but it is advice only: someone has to copy it into the Hive app by hand, and nobody does.

Some existing figures are also wrong in an empty house. Comfort Day counts a weekend away as hours of discomfort, and the Room Forecast assumes the schedule runs when it doesn't.

## Solution

hive-app works out whether Someone Is Home from the household's phones on the home network, as Pi-hole sees them, and takes over the thermostat:

- **Part A, the Comfort Schedule.** hive-app writes today's and tomorrow's Hive schedule from the Comfort Plan whenever the plan changes, and again when someone comes home. The house then heats to the learned Comfort Temperature through the Comfort Window, and to the Away Temperature at all other times. The household no longer maintains a schedule. It stands alone: it needs no presence detection.
- **Part B, presence.** Once no Phone has made a DNS lookup for 30 minutes, the house is No One Is Home. The thermostat goes to MANUAL at the Away Temperature (7 °C) and stays there, whatever anyone changes by hand, until a Phone is seen again. It then goes back to SCHEDULE. A thermostat that is OFF is left alone. Boosts still work from anywhere and never count as presence. If Pi-hole can't be read, control keeps the last known state for 10 minutes, then assumes someone is home; an ntfy alert follows at 30 minutes. A warning is logged if the house is empty at 03:05.

Each part can be off, dry-run (record and log only) or on. Presence control is only meaningful on top of the Comfort Schedule, so presence `on` requires the Comfort Schedule `on`; presence `dry_run` is allowed at any time. Presence is recorded whenever the `presence` section exists, whatever its `control`: `control` gates only the heating actions.

**Ship order.** Presence recording ships first. Its alerts, Comfort Day, the dashboard panel and the Away Override follow in any order. The Away Override can ship before the Comfort Schedule because a startup check, not ship order, keeps it from acting alone: until the Comfort Schedule is `on`, presence control can only be `off` or `dry_run`, so a week of would-be decisions is gathered while part A is built. The schedule-write spike can run at any time. The Comfort Schedule ships last, and adds the "write the schedule before restoring SCHEDULE" step to the Away Override.

Comfort Day stops counting minutes while No One Is Home as discomfort. The Room Forecast's current scenario holds the thermostat's target whenever it is in MANUAL mode, which covers the Away Override. A new dashboard panel shows the Presence Periods.

## User Stories

### Detecting Phones

1. As a householder, I want my phone recognised automatically from what Pi-hole knows about it, so that I don't have to look up or maintain MAC or IP addresses.
2. As a householder, I want phones from any manufacturer (iPhone, Pixel, Galaxy, Redmi, OnePlus, Motorola and others) recognised by the same fixed rule, so that a new phone works without configuration.
3. As a householder, I want tablets, watches, laptops and TVs never counted as phones, even when they share a phone brand, so that a tablet left at home doesn't keep the heating on.
4. As a householder, I want devices with factory MAC addresses ignored by the automatic rule, so that always-on household kit never counts as someone home.
5. As a householder, I want to add phone hostnames (device names) in config, so that a phone the rule can't recognise (renamed, or with private addressing switched off) still counts.
6. As a householder, I want to list hostnames to ignore in config, so that a device the rule wrongly calls a phone, or a phone that never leaves the house, doesn't count.
7. As a householder, I want guests' phones to count as someone home, so that visitors aren't left in a cold house.
8. As a householder, I want the same device record always given the same answer, so that the rule's behaviour can be predicted and tested.

### Deciding presence

9. As a householder, I want the house to count as No One Is Home once no Phone has made a DNS lookup for 30 minutes, so that a phone that is briefly quiet doesn't trigger the Away Override.
10. As a householder, I want the Away Delay configurable, so that I can lengthen it if a phone sleeps for longer.
11. As a householder, I want Someone Is Home to begin at the first lookup by any Phone, so that the heating comes back as soon as I'm home.
12. As a householder, I want boosts to never change presence, so that a boost started from the train doesn't count as being home.
13. As a householder, I want control to keep the last known presence for 10 minutes when Pi-hole can't be read, and then treat it as Someone Is Home, so that a brief Pi-hole restart changes nothing and a broken signal never keeps the heating down while I'm in.
14. As a householder, I want an ntfy alert when Pi-hole has been unreadable for 30 minutes, an hourly reminder while it lasts and a notice when it's back, all respecting quiet hours, so that I know presence control is not working.
15. As a householder, I want a warning logged when No One Is Home just after 3am, so that a phone that is home but not being seen is noticed.
16. As a householder, I want every Presence Period stored with its start and end, so that the models and the dashboard can tell when the house was empty.

### Away Override

17. As a householder, I want the thermostat switched to MANUAL at the Away Temperature when No One Is Home begins, so that an empty house isn't heated.
18. As a householder, I want the Away Temperature configurable with a default of 7 °C, so that I can choose how cold the house may get.
18a. As a householder, I want a thermostat I have switched OFF left OFF while No One Is Home and when I return, so that going out in summer doesn't turn the heating schedule back on.
19. As a householder, I want my Hive schedule left untouched by the Away Override, so that nothing has to be restored and one tap in the Hive app recovers if the app stops.
20. As a householder, I want the Away Override re-asserted at every poll while No One Is Home, so that the app's state wins over any hand change made while the house is empty.
21. As a householder, I want a running boost never interrupted by the Away Override or by its restore, so that I can warm the house before I get home and the boost runs in full after I arrive.
22. As a householder, I want the thermostat switched back to SCHEDULE when Someone Is Home begins after an Away Override (once any running boost has ended), so that normal heating resumes on its own.
23. As a householder, I want boosts started while No One Is Home to still count as Comfort Temperature evidence, as every boost does today.
24. As a householder, I want presence control to be `off`, `dry_run` or `on`, default `off`, with `on` refused unless the Comfort Schedule is `on`, so that I can watch a week of decisions before giving it control, and control only acts on top of the Comfort Schedule.
24a. As a householder, I want the thermostat put back to SCHEDULE once when control is switched from `on` to `dry_run` or `off` (or the presence section is removed) while the Away Override is in force, so that changing the setting while away never leaves the house at the Away Temperature.
25. As a householder, I want every intended Hive write recorded with whether it was a dry run and whether it succeeded, with a dry run recording one action per departure and return as `on` would, so that dry-run decisions can be compared with when I actually left and came home.
26. As a householder, I want a failed Hive write logged and retried at the next poll, so that a brief Hive outage doesn't leave the override half-applied.

### Models and dashboard

27. As a householder, I want minutes while No One Is Home left out of Comfort Day's below-comfort minutes, so that a weekend away doesn't read as a weekend of discomfort.
28. As a householder, I want the Room Forecast's current scenario to hold the thermostat's target whenever it is in MANUAL mode with no boost running, so that it doesn't predict heating that won't happen, whether the Away Override or I set MANUAL.
29. As a householder, I want a dashboard panel showing Someone Is Home, No One Is Home and Presence Unknown periods over time, so that I can see when the house was judged empty.

### Comfort Schedule (part A)

30. As a householder, I want hive-app to write today's and tomorrow's Hive schedule from the Comfort Plan, so that the house is heated to the learned comfort level without me maintaining a schedule.
31. As a householder, I want the Comfort Schedule to hold the Comfort Temperature from the pre-heat start to the Comfort Window's close and the Away Temperature at all other times, so that it heats exactly what the Comfort Plan modelled.
32. As a householder, I want the schedule written only when it differs from what Hive holds, so that Hive isn't written to every hour for nothing.
33. As a householder, I want the schedule rewritten from the latest Comfort Plan when Someone Is Home begins, before the thermostat goes back to SCHEDULE, so that I come home to the right schedule.
34. As a householder, I want every schedule write read back from Hive and recorded as failed if Hive didn't store it, so that a silently ignored write is noticed.
35. As a householder, I want the Comfort Schedule to be `off`, `dry_run` or `on`, default `off`, and to work without presence detection, so that it can be watched before it replaces my schedule.
35a. As a householder, I want a schedule write that Hive didn't store not repeated for an hour, so that a write Hive keeps rejecting doesn't repeat every two minutes.
36. As a maintainer, I want a spike that proves the schedule write on the live account, recording the accepted start-time grain and slot limit, before any production schedule write is built.

## Implementation Decisions

### Shape

hive-app is the only process that reads Pi-hole and the only process that writes to Hive ([ADR-0037](../adr/0037-presence-comes-from-phones-dns-activity-in-pi-hole-and-fails-safe-to-home.md), [ADR-0038](../adr/0038-the-away-override-holds-manual-mode-and-re-asserts-it-except-during-a-boost.md)). Two jobs cooperate through the database:

- A new **presence job** runs every 60 seconds, reads Pi-hole, decides presence and maintains `presence_period`. It never touches Hive, so a Hive outage doesn't stop presence being recorded.
- The existing **heating poll** (every 120 seconds) fetches and stores the Heating Status as today, then reads the latest Presence Period and the newest control actions, decides, writes and records. Each HiveSource verb already runs in its own Hive session (one `asyncio.run`, one Cognito refresh), so each write verb opens its own session too. Writes are rare (one per departure and return, one per hand change reverted, at most one Comfort Schedule write an hour), so this adds a handful of token refreshes a day against the roughly 720 the polls already make. Someone Is Home reaches the thermostat within about three to four minutes (Pi-hole's own update of a device's last lookup, the 60-second presence job, then the 120-second poll).

octopus-app reads `presence_period` the way it already reads `heating_status` and `heating_schedule`: through a table declared in its own MariaDB client.

### Configuration (hive-app `config.yml`)

Two new optional sections. `comfort_schedule` (part A) holds the Away Temperature and stands alone. `presence` (part B) needs no `comfort_schedule` section to record presence or to dry-run, but its `on` needs the Comfort Schedule `on`. Without either section nothing in this spec runs, except the one-time restore below. The Pi-hole password is a secret and lives only in the Pi's `config.yml`. The repo template carries blanks and a comment, like `hive.password`.

```yaml
comfort_schedule:
  control: off            # off | dry_run | on; part A
  away_temperature: 7.0   # held outside the Comfort Window, and by the Away Override

presence:
  control: off            # off | dry_run | on; on needs comfort_schedule.control: on
  pihole:
    url: http://pihole.example.lan
    password:             # a Pi-hole app password
  away_delay_minutes: 30
  phone_hostnames: []     # e.g. ["Alex-s-Phone"]; case-insensitive, matched on the name before the first dot
  ignored_hostnames: []   # wins over both the Phone Rule and phone_hostnames
```

`control` is read as a word whichever way YAML spells it: an unquoted `off` or `on` (YAML 1.1 booleans) means "off" or "on". Every YAML boolean spelling is read the same way, so `no` means "off" and `yes` means "on".

Settings validation refuses at startup:

- an unknown key inside `presence` or `presence.pihole` (a typo such as `ignored_hostname` would otherwise be dropped silently);
- a non-positive Away Delay;
- an Away Temperature outside 5–32 °C (the library's defaults for this thermostat type) or not on a half degree;
- a blank Pi-hole URL or password, or a URL that doesn't start with `http://` or `https://`, while a `presence` section is present;
- `presence.control: on` unless `comfort_schedule.control` is `on`.

The Away Override reads the Away Temperature from `comfort_schedule`, with the 7 °C default when that section is absent (only possible in presence `dry_run`). The Away Override issue introduces `comfort_schedule.away_temperature` and refuses `presence.control: on` outright. The Comfort Schedule issue adds `comfort_schedule.control` and relaxes the refusal to "unless `comfort_schedule.control` is `on`".

### Pi-hole source

A **PiholeSource** Protocol with one verb, returning every device Pi-hole knows: its MAC address, the hostnames attached to its addresses, and the time of its last DNS lookup (none if it has never made one). The real implementation talks to the Pi-hole v6 REST API:

- `POST /api/auth` with the app password returns a session ID. The ID is sent as the `X-FTL-SID` header and reused across polls. A 401 triggers one fresh login and one retry. The session is released with `DELETE /api/auth` at shutdown, best effort, because Pi-hole caps concurrent sessions.
- `GET /api/network/devices?max_devices=<n>&max_addresses=<m>` returns the devices, most recent lookup first (about 11 KB for the 43 devices on this network). Both parameters are always sent, as named constants well above this network's size (for example 500 and 20). Pi-hole's API documents a default of 10 devices, and one real device already returned exactly 3 addresses, which looks like the address cap in effect. Each device's `hwaddr`, `ips[].name` and `lastQuery` (epoch seconds, 0 = never) are what the rule needs.
- `hwaddr` is not always a MAC address. FTL stores `ip-<address>` for a client whose MAC it can't find (for example an IPv6 client missing from the neighbour cache). The source passes it through unchanged, and the Phone Rule handles it.
- Requests time out after 10 seconds. Any failure (timeout, connection error, rejected password, malformed body) surfaces as one source error. A body counts as malformed when `devices` or a device's `ips` is not a list (an empty `{}` or `""` is not an empty list), or a field has the wrong type. A list with no devices is also a source error: this network has dozens of devices, so an empty list means Pi-hole has lost its table (for example after a restart), and recording No One Is Home from it would be a false absence. The password never appears in logs or exceptions.

Unlike HiveApiSource, whose Cognito login can't be faked cheaply, the real client is plain HTTP and is tested with faked HTTP responses: session reuse, a fresh login on 401, the explicit `max_devices` and `max_addresses`, one source error for every failure, and the password absent from logs and errors. The dry-run week still verifies it against the real Pi-hole.

### Phone Rule

A pure module, ported from the prototype that matched exactly the three household phones among 43 real Pi-hole devices. The prototype's full word lists and table cases, with made-up names only, are in [phone-rule-prototype.md](../research/phone-rule-prototype.md); the lists below are trimmed. Fixed order; the first rule that decides wins:

1. The hostname is in `ignored_hostnames`: **not a Phone**.
2. The hostname is in `phone_hostnames`: **Phone**. This bypasses the MAC gate and word rules.
3. The hardware address is not a randomised MAC: **not a Phone**. Randomised means six colon-separated hex octets, locally administered and unicast: bit 0x02 of the first octet set, bit 0x01 clear. Anything that is not a MAC (FTL's `ip-<address>`, blank) fails this gate rather than raising.
4. No usable hostname (blank, `*`, `localhost`): **unknown**, which does not count.
5. The hostname is split into lowercase words, on non-alphanumerics and on letter/digit boundaries (`Alex-s-Tab-S8` → `alex, s, tab, s, 8`). A word from a non-phone group gives **not a Phone**. Exclusions run before phone words, so `Galaxy-Tab-S9` and `Pixel-Watch-3` are never phones.
6. Any of these gives **Phone**: a phone word, Android's pre-11 default `android-<hex ANDROID_ID>`, or a Samsung phone model code (`SM-` followed by A, S, G, F, N, M or E and three digits; T, X, P and R are tablets and watches).
7. Otherwise **unknown**.

A device with several hostnames is a Phone if any of them is. The word lists, trimmed from the prototype:

```python
_NOT_PHONE_TOKENS = (  # ordered; first group whose word appears decides
    ("tablet",   {"tab", "tablet", "ipad", "pad", "matepad", "mediapad", "kindle", "fire", ...}),
    ("wearable", {"watch", "band", "fit", "gear", "wear"}),
    ("computer", {"macbook", "imac", "mac", "laptop", "desktop", "pc", "chromebook",
                  "pixelbook", "book", "surface", "thinkpad", "windows", ...}),
    ("media",    {"tv", "chromecast", "roku", "firetv", "shield", "xbox", "playstation",
                  "nintendo", "switch", "echo", "sonos", ...}),
)
_PHONE_TOKENS = {
    "iphone", "phone", "smartphone", "mobile", "android",
    "pixel", "galaxy", "samsung", "xiaomi", "redmi", "poco", "oneplus", "nord", "motorola",
    "moto", "razr", "xperia", "nokia", "hmd", "realme", "oppo", "vivo", "iqoo", "honor",
    "huawei", "fairphone", "zenfone", "rog", "asus", "tcl", "alcatel", "zte", "nubia",
    "infinix", "tecno", "itel", "lg", "htc", "meizu", "lenovo", ...
}
```

The word lists rest on how phones name themselves. Since Android 11, a phone sends its Settings → About phone → Device name, by default the marketing name. Android converts it to ASCII, replaces every character outside `[A-Za-z0-9-]` with `-`, collapses repeats and caps it at 63 characters, so "Galaxy S24 Ultra" arrives as `Galaxy-S24-Ultra`. iPhones send their device name (`iPhone`, `Alex-s-iPhone`).

### Presence decision and Presence Periods

Each presence job run:

1. Reads the devices. A source error makes this run **Presence Unknown**.
2. Otherwise, counts the Phones whose last lookup is no more than the Away Delay before now (a lookup exactly 30:00 ago still counts): one or more is **Someone Is Home**, none is **No One Is Home**. The decision uses Pi-hole's own last-lookup times, so the first run after a restart is right immediately; there is no warm-up.
3. If the state differs from the open Presence Period, closes it (`ended_at` = now) and opens a new one (`started_at` = now). Otherwise it changes nothing. A restart continues the open period when the state is unchanged.

Transitions are logged at INFO with the number of Phones seen (Presence Unknown has no count). A Pi-hole failure is logged at WARNING once, when the period becomes Presence Unknown, and at DEBUG while it stays so, so an outage doesn't write a line a minute. Hostnames are logged at DEBUG only.

The presence job is scheduled like the Hive data watchdog, not through the retry-with-backoff wrapper: a Pi-hole error is already Presence Unknown, and anything else (for example a database error) is caught and logged, since the next run is 60 seconds away. A backoff would hold the worker for up to 15 minutes and skip the runs in between.

**`presence_period`** (hive-app model, Schema Sync creates it):

| column | type | notes |
| --- | --- | --- |
| `id` | int, auto, PK | |
| `state` | varchar(20) | `someone_home`, `no_one_home`, `unknown` |
| `started_at` | datetime, indexed | naive UTC, like `heating_status.polled_at` |
| `ended_at` | datetime, nullable | NULL while open; at most one open row |
| `phones_seen` | int | Phones counted when the period opened |

No hostnames or MAC addresses are stored.

**Signal-lost alert.** A watchdog in the shape of the Hive data-lost watchdog ([ADR-0031](../adr/0031-hive-data-lost-alert-replaces-the-reauth-alert.md)), driven by the open period: Presence Unknown for more than 30 minutes sends "hive-app: presence signal lost", reminders follow hourly, and a "presence signal restored" message goes out when it ends. All of them respect `ntfy.quiet_hours`. It reuses the ntfy notifier. Like the Hive data watchdog, it is not built without `ntfy` configured, and startup logs a warning that no live alert will be sent.

**03:05 warning.** A daily job at 03:05 in the configured timezone logs a WARNING if the open period is No One Is Home, including how long it has lasted. The time is a named constant: just after 3am, clear of the 02:00 and 01:00 clock-change hours.

### Away Override control

The HiveSource Protocol gains two write verbs. Like every existing verb, each runs in its own Hive session:

- **apply away override** (target). One low-level `api.setState(type, id, mode="MANUAL", target=<away>)` on the heating node, sending both fields in one POST so Hive never holds MANUAL at the old target. The library's `Heating.setMode` and `setTargetTemperature` helpers each send only one field, so they aren't used.
- **restore schedule**. `api.setState(type, id, mode="SCHEDULE")`.

Each re-reads the product in the same session afterwards and reports success only if Hive now shows what was sent. Every library exception is caught and reported as a failure, with the exception's type name as the detail. apyhiveapi doesn't return `False` on failure: `request` raises `HiveApiError` for any non-2xx answer and `HiveAuthError` for 401 or 403, and `setState` turns timeouts and connection errors into `HTTPError` through `self.error()`. The one exception is the re-read itself: `session.getDevices` returns `False` on a timeout or API error and keeps its cached product data, so a `False` is reported as `not_verified` rather than read back from the cache.

**Presence for control** is the open Presence Period's state, except Presence Unknown. Unknown open for under 10 minutes (a named constant) counts as the state of the period before it. From 10 minutes, or with no earlier period, it counts as Someone Is Home.

After storing the Heating Status, HeatingRetriever applies a pure control policy. Its inputs are the control setting, Presence for control, the status just fetched (mode, target, `boost_active`) and the newest control actions recorded with the same `dry_run` value. The first matching row wins:

| Presence for control | Thermostat | Override in force? | Action |
| --- | --- | --- | --- |
| No One Is Home | `boost_active` | any | none, the boost runs |
| No One Is Home | OFF | any | none, OFF is left alone |
| No One Is Home | MANUAL at Away Temperature | yes | none |
| No One Is Home | MANUAL at Away Temperature | no | none written; with control `on`, record `away_override` as `applied` with detail `adopted`, so the return restores it. In `dry_run` nothing is recorded: the real thermostat never moves, so there is no real override to adopt |
| No One Is Home | anything else | any | apply away override |
| Someone Is Home | `boost_active` | yes | none; the restore waits until the boost ends and Hive falls back to the override |
| Someone Is Home | OFF | yes | nothing sent; record `restore_schedule` as `skipped`, which ends the override |
| Someone Is Home | anything else | yes | restore schedule (the Comfort Schedule, when on, is written first) |
| Someone Is Home | any | no | none |

"Override in force" means the newest recorded `away_override` action, whatever its outcome (`applied`, `not_verified`, `failed` or a dry run), has no later `restore_schedule` action that applied, was a dry run, or was skipped. A `failed` or `not_verified` override counts because the write may have landed before the response or read-back went wrong (a timeout, say), and a return before the next poll must still restore it. A restore sent when nothing had landed is harmless in the usual case, because SCHEDULE is the state the thermostat is already in. The accepted exception: if Hive stays unreachable and the household sets MANUAL by hand before the next poll, the restore replaces that setting. That narrow window is the price of never leaving an empty-house override stranded. A `failed` or `not_verified` restore leaves the override in force, so the next poll retries. A hand-set MANUAL on the Away Temperature while away is adopted the same way, which is right: it is restored on return too. While the household is home and in control (for example MANUAL 21 °C for a party), the app writes nothing.

In `dry_run`, the policy runs and records the same actions with `dry_run` true and outcome `dry_run`, and logs "would set …" at INFO, but nothing is sent to Hive. Because the real thermostat never moves, a dry-run override in force stands in for "MANUAL at Away Temperature". A day out therefore records one `away_override` and one `restore_schedule`, as `on` would. Hand changes while away aren't simulated.

**Leaving `on` while the override is in force.** Whenever control isn't `on` (`dry_run`, `off`, or no `presence` section at all), each poll also checks the real (`dry_run` false) actions. If a real override is in force, it applies the Someone Is Home rows to that history: the thermostat is restored once (after any boost; skipped if OFF), and a failure is retried at the next poll. After that it does nothing. This check costs one indexed read per poll.

**`heating_control_action`** (hive-app model, Schema Sync creates it):

| column | type | notes |
| --- | --- | --- |
| `id` | int, auto, PK | |
| `acted_at` | datetime, indexed | naive UTC |
| `action` | varchar(30) | `away_override`, `restore_schedule`, `comfort_schedule` |
| `mode` | varchar(20), nullable | mode written, if any |
| `target` | double, nullable | target written, if any |
| `dry_run` | bool | |
| `outcome` | varchar(20) | `applied`, `failed`, `dry_run`, `skipped` (restore not needed: the thermostat was OFF), `not_verified` (Hive said yes but stored something else) |
| `detail` | varchar(255), nullable | short failure reason; never a secret |

A failed write is logged at WARNING and stored as `failed`. It is never raised out of the heating poll, because the Heating Status row is already stored and a retry with backoff would store a second one (same reasoning as the weekly-schedule write).

### octopus-app changes

- **Comfort Day** (ComfortDayJob): minutes inside a `no_one_home` Presence Period don't count towards the below-comfort minutes of Counted Hours. Settled days are never restated, so this applies to days settled after deploy. Counted Hours and Comfort Temperature learning are unchanged in code: boosts during No One Is Home still count as evidence. Their inputs will shift, which is expected. Counted Hours are every hour of day heated at least once in the past year, so absences barely move them, and under the Comfort Schedule they settle on the Comfort Window plus its pre-heat.
- **Room Forecast** (RoomForecastJob): whenever the newest heating poll is MANUAL with no boost running, the current scenario holds that poll's stored target for the whole horizon instead of following the Hive schedule. Today only OFF is handled; MANUAL follows the schedule. This covers the Away Override exactly when it has actually been applied (not in `off` or `dry_run`), and also a hand-set MANUAL. `read_latest_heating_status` also returns `target_temp` and `boost_active`. octopus-app does not read `presence_period` for this. During a boost the scenario is unchanged. Future absences aren't predicted. A Day-Ahead Forecast frozen while MANUAL keeps that assumption. The plan scenario is unchanged.

### Dashboard

A state-timeline panel on the gas and heating dashboard shows the last 7 days of Presence Periods in London time, one colour per state. Its SQL goes into the gas-heating queries document and is covered by the existing dashboard and queries-document tests. Before import, the SQL is measured on a throwaway copy of the database, never on the Pi's live MariaDB. It passes if that copy's peak memory stays under the live server's 512 MB cap.

### Comfort Schedule (part A)

- **Spike first.** It depends on nothing else in this spec and can run at any time. On the live account, the spike writes a made-up schedule to one weekday (for example 05:40–06:25 at 19 °C, Away Temperature otherwise) via the raw POST below, reads it back, and restores the original week. A research note records whether Hive accepted it, the start-time grain (Hive's app uses coarser steps than the plan's 2-minute marks), the slot limit per day and whether a 200 can come back unstored. It also records the 2.0 beta's `set_schedule` validation rules from its source at a named version, since the rules below are taken from it and no note records them yet. The production write depends on its findings.
- **Write path.** pyhive-integration 1.0.10 has no schedule setter, and its `setState` turns every value into a quoted string, so a nested schedule can't go through it. hive-app sends `POST /nodes/heating/{id}` with the JSON body `{"schedule": {...}}` through 1.0.10's authenticated `api.request`, copying the 2.0 beta's `set_schedule` rules (2.0.0b5, [spike note](../research/hive-schedule-write-spike.md)):
  - all seven days are sent;
  - each day is non-empty, with integer `start` minutes 0–1439 and unique (the beta sorts the slots itself; hive-app writes them sorted), each slot shaped `{"start": <minutes>, "value": {"target": <°C>}}`;
  - the product is re-read afterwards, and the write counts only if Hive stored exactly what was sent. A differing first read is read again after a few seconds before the write is called `not_verified`: the spike saw a whole-week write read back different 2 seconds later and identical minutes later.

  hive-app adds two checks of its own, which neither the beta nor Hive enforces: the first entry of each day is at 00:00, and targets are on half degrees. The spike proved on the live account that Hive stores one-minute starts, any target (19.3 was kept) and at least 48 slots a day, and showed no 200-but-unstored write.

  Keeping 1.0.10 avoids porting the login and resume path to the beta's renamed API. `pyproject.toml` today allows `pyhive-integration>=1.0.9`, so the next dependency update would pull 2.0 once it is stable and break both login and this POST; it is pinned to `>=1.0.10,<2`. A follow-up issue moves to the library's `set_schedule` once 2.0 is released stable. Like the override verbs, the schedule write opens its own Hive session and does its read-back in it, and every library exception becomes a `failed` action.
- **Building the week.** The base is the Weekly Schedule hive-app already reads in the same poll. Today's and tomorrow's weekdays (London days) are replaced from the newest `room_forecast_slot` rows with scenario `plan`, converted from naive UTC instants to London clock minutes, each instant at its own London offset. Plan slots run from the pre-heat to the window's close (at latest midnight), so they never touch the 01:00–02:00 clock-change hours; clock-change days need no special case, and one test covers one. Each day becomes: the Away Temperature at 00:00, the slot's target from its start, and the Away Temperature from its end. A slot ending at midnight needs no closing entry. The start rounds earlier and the end rounds later to whole minutes, the grain the spike proved, so the Comfort Window is never shortened. A day with no plan slot keeps its existing entries, logged at INFO on the poll that writes or records the week (not on every poll, or a day without a slot would log every 2 minutes). The other five weekdays keep whatever was last written.
- **When.** At every heating poll, the Comfort Schedule is written only if the built week differs from the Weekly Schedule Hive holds. Because the plan is recomputed hourly, in practice that means after each plan run that moves a slot. On the poll that restores SCHEDULE after an Away Override, the Comfort Schedule is written before the restore. The write continues while No One Is Home, so the schedule is current on return. Each write is recorded as a `comfort_schedule` action. The next poll's Weekly Schedule capture stores the new week, as today. A `failed` write is retried at the next poll. After a `not_verified` write, the Comfort Schedule isn't written again for 60 minutes (one plan cycle), with a WARNING. Otherwise a Hive that stores something other than what was sent would differ again at every poll and be rewritten every 2 minutes.
  - **No readable Weekly Schedule.** If this poll could not read Hive's week, no week is built and nothing is written, with a WARNING: the five untouched days come from it, so a week built without it would overwrite them blind.
  - **Dry run.** Because nothing is written, Hive's week never changes, so the built week would differ at every poll. A dry run therefore pretends its write landed, as the Away Override's does: it stores a `dry_run` action only when the built week differs from the week of the newest `dry_run` `comfort_schedule` action. That week is compared by a SHA-256 hex fingerprint kept in the action's `detail`, which survives a restart and needs no new column. The would-be week is logged at INFO.
  - **Refusals name their rule.** A week that breaks a write rule is refused before anything is sent. The rule's message (it holds a weekday and the rule, never input values) is kept in the WARNING and in the `failed` action's `detail` as `ValueError: <message>`; any other exception keeps only its type name. A dry run applies the same rules: a would-be week that `on` would refuse is logged as a WARNING and recorded as `failed` with `dry_run` true and that message as `detail`, once per distinct message (compared like the fingerprint), so the dry-run period shows a refusal before `on` is switched on.
- Hand edits made in the Hive app are overwritten at the next difference. That is the point of the Comfort Schedule.

### Modules touched

- hive-app:
  - settings (new `presence` and `comfort_schedule` models; cross-section validation)
  - main (presence job every 60 s, scheduled like the watchdog; 03:05 job; signal-lost watchdog job; wiring and shutdown logout)
  - HeatingRetriever and the HiveSource Protocol (control policy, including Presence for control and the leaving-`on` restore; write verbs)
  - HiveApiSource (write verbs, each in its own session with read-back; part A schedule POST and verification)
  - `pyproject.toml` (pin `pyhive-integration>=1.0.10,<2`)
  - new PiholeSource with its Pi-hole API client, Phone Rule, presence decision, PresenceRetriever and signal-lost watchdog
  - ntfy notifier (presence messages)
  - MariaDB model and client (two tables, reads and writes)
  - `config.yml.template`
- octopus-app: MariaDB client (`presence_period` table declaration and reads; `read_latest_heating_status` also returns target and boost); ComfortDayJob; RoomForecastJob.
- Grafana: gas and heating dashboard JSON; gas-heating queries document.
- Docs: glossary (already updated); ADR-0037 and ADR-0038 (written); Phone Rule prototype research note (written); heating guide (how presence control and the Comfort Schedule behave, including the overnight false away below).

## Testing Decisions

Good tests here drive a public entry point with fixed inputs and a fixed clock, then assert on stored rows, recorded writes and log lines. They never assert on private helpers. Fakes sit only at the external boundaries (Pi-hole, Hive), with the real MariaDB test client underneath, as in the existing hive-app and octopus-app suites.

- **PiholeApiSource**, with HTTP faked at the boundary (`responses`), as listed under Pi-hole source above.
- **Phone Rule, tested directly** (agreed exception: a pure rule many cases flow through). Table-driven Given/When/Then cases, starting from the prototype's cases in the research note:
  - phone hostnames from every listed manufacturer;
  - tablets, watches, computers and media devices that share phone brands;
  - Samsung model codes for phones and for tablets;
  - Android's legacy default;
  - missing and meaningless hostnames;
  - the MAC gate, including multicast, FTL's `ip-<address>` and a blank address (not a Phone, never raising), and a configured phone hostname on an `ip-` address (a Phone);
  - configured extra and ignored hostnames, including precedence and case-insensitivity;
  - determinism.

  Only made-up hostnames and MAC addresses; never real household ones (the repo is public).
- **PresenceRetriever.refresh(now)** with a fake PiholeSource returning device lists. It covers:
  - Someone Is Home and No One Is Home at the Away Delay boundary (29:59 and 30:00 home, 30:01 away);
  - opening and closing Presence Periods, and an unchanged state doing nothing;
  - a restart continuing the open period;
  - a source error giving Presence Unknown, and any other error logged without raising;
  - the signal-lost alert, reminder, restored message and quiet hours (fake notifier and fake clock, like the Hive data-lost watchdog tests);
  - the 03:05 warning, present when the house is empty and absent otherwise.
- **HeatingRetriever.refresh(now)** with the existing fake HiveSource extended with the write verbs, recording calls. It covers:
  - every row of the control-policy table, including a boost skip, a hand change reverted, OFF left alone, a restore waiting for a boost to end and a restore skipped for OFF;
  - Presence for control: Unknown at 9:59 keeps the earlier state, Unknown at 10:00 counts as home, and Unknown with no earlier period counts as home;
  - a failed restore retried at the next poll;
  - dry-run recording without calls, with one `away_override` and one `restore_schedule` over a whole absence;
  - `off` making no calls and recording nothing;
  - leaving `on` while a real override is in force (to `dry_run`, to `off`, and with no `presence` section): one restore, retried if it fails, then nothing;
  - a failed write (the fake raising) stored as `failed` and not raised.

  For part A it also covers:
  - building the week (made-up 05:40–06:25 slots), rounding to the grain, a midnight end, a day without a plan slot and a clock-change day;
  - writing only on a difference;
  - writing before restore on return;
  - `not_verified` when the read-back differs, and no rewrite for the next 60 minutes;
  - the Comfort Schedule written with no `presence` section.
- **ComfortDayJob.refresh** and **RoomForecastJob.refresh**, through their existing seams. They cover:
  - below-comfort minutes excluded inside `no_one_home` only (seeded `presence_period` rows);
  - the current scenario holding the stored target when the newest poll is MANUAL with no boost, and following the schedule when it is SCHEDULE or boosting (seeded `heating_status` rows).
- **Schema Sync tests** for both new tables, and settings tests for the new config (defaults, absent sections, refused values including presence `on` without the Comfort Schedule `on` and a blank Pi-hole password), in the shape of the existing schema-sync and config tests.
- **Prior art:** test_heating_retrieval (fake HiveSource over real MariaDB), test_hive_data_watchdog and test_ntfy_data_notifier (alerts and quiet hours), test_comfort_day_job and test_room_forecast_job (seeded octopus-app jobs), test_hive_config_settings, the `*_schema_sync` tests, test_grafana_dashboards and test_grafana_queries_document.
- **Live verification, not unit tests:** the real Pi-hole client's behaviour against a real Pi-hole and Hive writes are checked by a dry-run week compared against actual departures and returns before `control: on`. The schedule write is proven by the spike, then by a `comfort_schedule: dry_run` period before `on`.

## Out of Scope

- Hive geofencing and Hive holiday mode.
- Per-person presence, or acting on who is home rather than whether anyone is.
- Predicting future absences in the Room Forecast or the Comfort Plan.
- Hot water, TRVs or multiple heating zones (the account has one heating zone and no hot water control).
- Excluding boosts during No One Is Home from Comfort Temperature learning (decided: they count).
- Restating Comfort Days settled before deploy.
- Upgrading to pyhive-integration 2.0 (a follow-up issue once it is stable).
- Electricity-based occupancy and backfilling presence history from Pi-hole's query log.

## Further Notes

- **What the design was based on (2026-11-17).** Over 24 hours, the two busiest household phones never went more than 10 minutes between DNS lookups at home; the quietest went up to 43 minutes overnight. One phone's departure and another's arrival were both clear to the minute. The household chose a 30-minute Away Delay knowing the quietest phone may occasionally read as away overnight, and confirmed it after review. When that phone is the only one home, the Away Override can hold the morning pre-heat at the Away Temperature until the phone's next lookup, so the room may still be cold when the Comfort Window opens. The household accepted this rather than adding a longer night-time delay. The heating guide documents it, and the Away Delay can be lengthened in config.
- Phones with a randomised MAC may rotate it. Hostnames survive rotation, which is why identity rests on them. Some phones send no hostname: iPhones with a private address sometimes, and Android 15 has a "Send device name" switch. Such phones are invisible unless named in config, and the 03:05 warning is how that is noticed.
- Never commit real hostnames, MAC addresses, the Pi-hole password or the real Hive schedule. Specs, tests and issues use made-up names and the made-up 05:40–06:25 at 19 °C slot.
- Rollout order: once the Away Override ships, run `presence.control: dry_run` and compare `heating_control_action` rows with reality while part A is built. Part A goes spike → `comfort_schedule: dry_run` → `on`. Only then is `presence.control: on` accepted at startup.
- **Review of 2026-11-17.** A review of the first draft changed: writes use their own Hive sessions (the "same session" claim didn't match the code); OFF is left alone; dry runs pretend their writes landed; leaving `on` mid-override restores once; Presence Unknown has a 10-minute grace for control; restores wait for a running boost; the Room Forecast keys on MANUAL mode, not presence; the Comfort Schedule stands alone and presence `on` is refused at startup without it (the Away Temperature moves under `comfort_schedule`); the Pi-hole query names its limits and the Phone Rule handles non-MAC addresses; the library is pinned below 2.0; a `not_verified` write backs off for an hour.
- Glossary terms introduced in the design sessions: Phone, Phone Rule, No One Is Home / Someone Is Home, Presence Unknown, Presence Period, Away Override, Away Temperature, Comfort Schedule. Presence-Based Heating Control, Comfort Plan and hive-app have been updated.

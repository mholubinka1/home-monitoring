# Presence-Based Heating Control and the Comfort Schedule

## Problem Statement

The heating follows a hand-set Hive schedule whether or not anyone is in. When the household goes out for the day or away for a weekend, the boiler still heats an empty house to the full schedule, and the only way to stop it is to remember to change the thermostat. The Comfort Plan already works out the least heating that keeps the room comfortable, but it is advice only: someone has to copy it into the Hive app by hand, and nobody does.

Some existing figures are also wrong in an empty house. Comfort Day counts a weekend away as hours of discomfort, and the Room Forecast assumes the schedule runs when it doesn't.

## Solution

hive-app works out whether Someone Is Home from the household's phones on the home network, as Pi-hole sees them, and takes over the thermostat:

- **Part B, presence (ships first).** Once no Phone has made a DNS lookup for 30 minutes, the house is No One Is Home. The thermostat goes to MANUAL at the Away Temperature (7 °C) and stays there, whatever anyone changes by hand, until a Phone is seen again. It then goes back to SCHEDULE. Boosts still work from anywhere and never count as presence. If Pi-hole can't be read, the app assumes someone is home and sends an ntfy alert. A warning is logged if the house is empty at 03:05. Each setting can be off, dry-run (record and log only) or on.
- **Part A, the Comfort Schedule (ships after B).** hive-app writes today's and tomorrow's Hive schedule from the Comfort Plan whenever the plan changes, and again when someone comes home. The house then heats to the learned Comfort Temperature through the Comfort Window, and to the Away Temperature at all other times. The household no longer maintains a schedule.

Comfort Day stops counting minutes while No One Is Home as discomfort. The Room Forecast's current scenario assumes the thermostat's actual target while the Away Override is in force. A new dashboard panel shows the Presence Periods.

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
13. As a householder, I want presence treated as Someone Is Home when Pi-hole can't be read, so that a broken signal never turns the heating down while I'm in.
14. As a householder, I want an ntfy alert when Pi-hole has been unreadable for 30 minutes, an hourly reminder while it lasts and a notice when it's back, all respecting quiet hours, so that I know presence control is not working.
15. As a householder, I want a warning logged when No One Is Home just after 3am, so that a phone that is home but not being seen is noticed.
16. As a householder, I want every Presence Period stored with its start and end, so that the models and the dashboard can tell when the house was empty.

### Away Override

17. As a householder, I want the thermostat switched to MANUAL at the Away Temperature when No One Is Home begins, so that an empty house isn't heated.
18. As a householder, I want the Away Temperature configurable with a default of 7 °C, so that I can choose how cold the house may get.
19. As a householder, I want my Hive schedule left untouched by the Away Override, so that nothing has to be restored and one tap in the Hive app recovers if the app stops.
20. As a householder, I want the Away Override re-asserted at every poll while No One Is Home, so that the app's state wins over any hand change made while the house is empty.
21. As a householder, I want a running boost never interrupted by the Away Override, so that I can warm the house before I get home.
22. As a householder, I want the thermostat switched back to SCHEDULE when Someone Is Home begins after an Away Override, so that normal heating resumes on its own.
23. As a householder, I want boosts started while No One Is Home to still count as Comfort Temperature evidence, as every boost does today.
24. As a householder, I want presence control to be `off`, `dry_run` or `on`, default `off`, so that I can watch a week of decisions before giving it control.
25. As a householder, I want every intended Hive write recorded with whether it was a dry run and whether it succeeded, so that dry-run decisions can be compared with when I actually left and came home.
26. As a householder, I want a failed Hive write logged and retried at the next poll, so that a brief Hive outage doesn't leave the override half-applied.

### Models and dashboard

27. As a householder, I want minutes while No One Is Home left out of Comfort Day's below-comfort minutes, so that a weekend away doesn't read as a weekend of discomfort.
28. As a householder, I want the Room Forecast's current scenario to assume the thermostat's actual target while the Away Override is in force, so that it doesn't predict heating that won't happen.
29. As a householder, I want a dashboard panel showing Someone Is Home, No One Is Home and Presence Unknown periods over time, so that I can see when the house was judged empty.

### Comfort Schedule (part A)

30. As a householder, I want hive-app to write today's and tomorrow's Hive schedule from the Comfort Plan, so that the house is heated to the learned comfort level without me maintaining a schedule.
31. As a householder, I want the Comfort Schedule to hold the Comfort Temperature from the pre-heat start to the Comfort Window's close and the Away Temperature at all other times, so that it heats exactly what the Comfort Plan modelled.
32. As a householder, I want the schedule written only when it differs from what Hive holds, so that Hive isn't written to every hour for nothing.
33. As a householder, I want the schedule rewritten from the latest Comfort Plan when Someone Is Home begins, before the thermostat goes back to SCHEDULE, so that I come home to the right schedule.
34. As a householder, I want every schedule write read back from Hive and recorded as failed if Hive didn't store it, so that a silently ignored write is noticed.
35. As a householder, I want the Comfort Schedule to be `off`, `dry_run` or `on`, default `off`, so that it can be watched before it replaces my schedule.
36. As a maintainer, I want a spike that proves the schedule write on the live account, recording the accepted start-time grain and slot limit, before any production schedule write is built.

## Implementation Decisions

### Shape

hive-app is the only process that reads Pi-hole and the only process that writes to Hive ([ADR-0037](../adr/0037-presence-comes-from-phones-dns-activity-in-pi-hole-and-fails-safe-to-home.md), [ADR-0038](../adr/0038-the-away-override-holds-manual-mode-and-re-asserts-it-except-during-a-boost.md)). Two jobs cooperate through the database:

- A new **presence job** runs every 60 seconds, reads Pi-hole, decides presence and maintains `presence_period`. It never touches Hive, so a Hive outage doesn't stop presence being recorded.
- The existing **heating poll** (every 120 seconds, one Hive session per poll) reads the latest Presence Period after fetching the Heating Status. In the same session it applies the Away Override or restores SCHEDULE, and in part A writes the Comfort Schedule. This adds no Hive sessions and so no extra Cognito token refreshes. Someone Is Home reaches the thermostat within about three minutes.

octopus-app reads `presence_period` the way it already reads `heating_status` and `heating_schedule`: through a table declared in its own MariaDB client.

### Configuration (hive-app `config.yml`)

A new optional `presence` section. Without it, nothing in this spec runs. Part A's own switch sits under `comfort_schedule`. The Pi-hole password is a secret and lives only in the Pi's `config.yml`. The repo template carries blanks and a comment, like `hive.password`.

```yaml
presence:
  control: off            # off | dry_run | on
  pihole:
    url: http://pihole.example.lan
    password:             # a Pi-hole app password
  away_delay_minutes: 30
  away_temperature: 7.0   # Hive accepts half degrees
  phone_hostnames: []     # e.g. ["Alex-s-Phone"]; case-insensitive, matched on the name before the first dot
  ignored_hostnames: []   # wins over both the Phone Rule and phone_hostnames

comfort_schedule:
  control: off            # off | dry_run | on; part A
```

Settings validation refuses at startup a non-positive Away Delay, or an Away Temperature outside Hive's accepted range or not on a half degree.

### Pi-hole source

A **PiholeSource** Protocol with one verb, returning every device Pi-hole knows: its MAC address, the hostnames attached to its addresses, and the time of its last DNS lookup (none if it has never made one). The real implementation talks to the Pi-hole v6 REST API:

- `POST /api/auth` with the app password returns a session ID. The ID is sent as the `X-FTL-SID` header and reused across polls. A 401 triggers one fresh login and one retry. The session is released with `DELETE /api/auth` at shutdown, best effort, because Pi-hole caps concurrent sessions.
- `GET /api/network/devices` returns all devices in one call (about 11 KB on this network). Each device's `hwaddr`, `ips[].name` and `lastQuery` (epoch seconds, 0 = never) are what the rule needs.
- Requests time out after 10 seconds. Any failure (timeout, connection error, rejected password, malformed body) surfaces as one source error. The password never appears in logs or exceptions.

Like HiveApiSource, the real client is not unit-tested. The dry-run week verifies it.

### Phone Rule

A pure module, ported from the prototype that matched exactly the three household phones among 43 real Pi-hole devices. Fixed order; the first rule that decides wins:

1. The hostname is in `ignored_hostnames`: **not a Phone**.
2. The hostname is in `phone_hostnames`: **Phone**. This bypasses the MAC gate and word rules.
3. The MAC is not randomised: **not a Phone**. Randomised means locally administered and unicast: bit 0x02 of the first octet set, bit 0x01 clear.
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
2. Otherwise, counts the Phones whose last lookup is within the Away Delay of now: one or more is **Someone Is Home**, none is **No One Is Home**. The decision uses Pi-hole's own last-lookup times, so the first run after a restart is right immediately; there is no warm-up.
3. If the state differs from the open Presence Period, closes it (`ended_at` = now) and opens a new one (`started_at` = now). Otherwise it changes nothing. A restart continues the open period when the state is unchanged.

Transitions are logged at INFO with the number of Phones seen. Hostnames are logged at DEBUG only.

**`presence_period`** (hive-app model, Schema Sync creates it):

| column | type | notes |
|---|---|---|
| `id` | int, auto, PK | |
| `state` | varchar(20) | `someone_home`, `no_one_home`, `unknown` |
| `started_at` | datetime, indexed | naive UTC, like `heating_status.polled_at` |
| `ended_at` | datetime, nullable | NULL while open; at most one open row |
| `phones_seen` | int | Phones counted when the period opened |

No hostnames or MAC addresses are stored.

**Signal-lost alert.** A watchdog in the shape of the Hive data-lost watchdog ([ADR-0031](../adr/0031-hive-data-lost-alert-replaces-the-reauth-alert.md)), driven by the open period: Presence Unknown for more than 30 minutes sends "hive-app: presence signal lost", reminders follow hourly, and a "presence signal restored" message goes out when it ends. All of them respect `ntfy.quiet_hours`. It reuses the ntfy notifier. Without `ntfy` configured, it only logs.

**03:05 warning.** A daily job at 03:05 in the configured timezone logs a WARNING if the open period is No One Is Home, including how long it has lasted. The time is a named constant: just after 3am, clear of the 02:00 and 01:00 clock-change hours.

### Away Override control

The HiveSource Protocol gains two write verbs, both used inside the heating poll's existing session:

- **apply away override** (target). One `setState(mode="MANUAL", target=<away>)` request on the heating node, sending both fields in one POST so Hive never holds MANUAL at the old target.
- **restore schedule**. `setMode("SCHEDULE")`.

Each reports success or failure. apyhiveapi's own boolean is combined with the product data it re-reads afterwards.

After fetching the Heating Status, HeatingRetriever applies a pure control policy. Its inputs are the control setting, the open Presence Period, the status just fetched (mode, target, `boost_active`) and the newest control action recorded with the same `dry_run` value:

| Presence | Thermostat | Override in force? | Action |
|---|---|---|---|
| No One Is Home | `boost_active` | any | none, the boost runs |
| No One Is Home | MANUAL at Away Temperature | any | none |
| No One Is Home | anything else | any | apply away override |
| Someone Is Home or Unknown | any | yes | restore schedule (part A: write the Comfort Schedule first) |
| Someone Is Home or Unknown | any | no | none |

"Override in force" means the newest recorded `away_override` action that applied, or was a dry run, has no later `restore_schedule` action that applied, or was a dry run. A failed restore leaves the override in force, so the next poll retries. While the household is home and in control (for example MANUAL 21 °C for a party), the app writes nothing.

In `dry_run`, the policy runs and records the same actions with `dry_run` true and outcome `dry_run`, and logs "would set …" at INFO, but nothing is sent to Hive. In `off`, the policy doesn't run.

**`heating_control_action`** (hive-app model, Schema Sync creates it):

| column | type | notes |
|---|---|---|
| `id` | int, auto, PK | |
| `acted_at` | datetime, indexed | naive UTC |
| `action` | varchar(30) | `away_override`, `restore_schedule`, `comfort_schedule` |
| `mode` | varchar(20), nullable | mode written, if any |
| `target` | double, nullable | target written, if any |
| `dry_run` | bool | |
| `outcome` | varchar(20) | `applied`, `failed`, `dry_run`, `not_verified` (part A: Hive said yes but stored something else) |
| `detail` | varchar(255), nullable | short failure reason; never a secret |

A failed write is logged at WARNING and stored as `failed`. It is never raised out of the heating poll, because the Heating Status row is already stored and a retry with backoff would store a second one (same reasoning as the weekly-schedule write).

### octopus-app changes

- **Comfort Day** (ComfortDayJob): minutes inside a `no_one_home` Presence Period don't count towards the below-comfort minutes of Counted Hours. Settled days are never restated, so this applies to days settled after deploy. Counted Hours and Comfort Temperature learning are unchanged: boosts during No One Is Home still count as evidence.
- **Room Forecast** (RoomForecastJob): while the open Presence Period is `no_one_home`, the current scenario holds the thermostat's latest stored target for the whole horizon instead of the Hive schedule. Future absences aren't predicted. A Day-Ahead Forecast frozen during an absence keeps that assumption. The plan scenario is unchanged.

### Dashboard

A state-timeline panel on the gas and heating dashboard shows the last 7 days of Presence Periods in London time, one colour per state. Its SQL goes into the gas-heating queries document and is covered by the existing dashboard and queries-document tests. Before import, the SQL is measured on a throwaway copy of the database, never on the Pi's live MariaDB.

### Comfort Schedule (part A)

- **Spike first.** On the live account, the spike writes a made-up schedule to one weekday (for example 05:40–06:25 at 19 °C, Away Temperature otherwise) via the raw POST below, reads it back, and restores the original week. A research note records whether Hive accepted it, the start-time grain (Hive's app uses coarser steps than the plan's 2-minute marks), the slot limit per day and whether a 200 can come back unstored. The production write depends on its findings.
- **Write path.** pyhive-integration 1.0.10 has no schedule setter, and its `setState` turns every value into a quoted string, so a nested schedule can't go through it. hive-app sends `POST /nodes/heating/{id}` with the JSON body `{"schedule": {...}}` through 1.0.10's authenticated `api.request`, copying the 2.0 beta's `set_schedule` rules:
  - all seven days are sent;
  - each day is non-empty, with integer `start` minutes 0–1439, unique and sorted, the first at 0;
  - targets are on half degrees;
  - the product is re-read afterwards, and the write counts only if Hive stored exactly what was sent.

  Keeping 1.0.10 avoids porting the login and resume path to the beta's renamed API. A follow-up issue moves to the library's `set_schedule` once 2.0 is released stable.
- **Building the week.** The base is the Weekly Schedule hive-app already reads in the same poll. Today's and tomorrow's weekdays (London days) are replaced from the newest `room_forecast_slot` rows with scenario `plan`, converted from naive UTC instants to London clock minutes. Each day becomes: the Away Temperature at 00:00, the slot's target from its start, and the Away Temperature from its end. A slot ending at midnight needs no closing entry. The start rounds earlier and the end rounds later to the grain the spike proves, so the Comfort Window is never shortened. A day with no plan slot keeps its existing entries, logged at INFO. The other five weekdays keep whatever was last written.
- **When.** At every heating poll, the Comfort Schedule is written only if the built week differs from the Weekly Schedule Hive holds. Because the plan is recomputed hourly, in practice that means after each plan run that moves a slot. On the poll that restores SCHEDULE after an Away Override, the Comfort Schedule is written before the restore. The write continues while No One Is Home, so the schedule is current on return. Each write is recorded as a `comfort_schedule` action. The next poll's Weekly Schedule capture stores the new week, as today.
- Hand edits made in the Hive app are overwritten at the next difference. That is the point of the Comfort Schedule.

### Modules touched

- hive-app:
  - settings (new `presence` and `comfort_schedule` models)
  - main (presence job every 60 s; 03:05 job; signal-lost watchdog job; wiring and shutdown logout)
  - HeatingRetriever and the HiveSource Protocol (control policy; write verbs)
  - HiveApiSource (write verbs; part A schedule POST and verification)
  - new PiholeSource with its Pi-hole API client, Phone Rule, presence decision, PresenceRetriever and signal-lost watchdog
  - ntfy notifier (presence messages)
  - MariaDB model and client (two tables, reads and writes)
  - `config.yml.template`
- octopus-app: MariaDB client (`presence_period` table declaration and reads); ComfortDayJob; RoomForecastJob.
- Grafana: gas and heating dashboard JSON; gas-heating queries document.
- Docs: glossary (already updated); ADR-0037 and ADR-0038 (written); heating guide (how presence control and the Comfort Schedule behave).

## Testing Decisions

Good tests here drive a public entry point with fixed inputs and a fixed clock, then assert on stored rows, recorded writes and log lines. They never assert on private helpers. Fakes sit only at the external boundaries (Pi-hole, Hive), with the real MariaDB test client underneath, as in the existing hive-app and octopus-app suites.

- **Phone Rule, tested directly** (agreed exception: a pure rule many cases flow through). Table-driven Given/When/Then cases, as in the prototype's 62 tests:
  - phone hostnames from every listed manufacturer;
  - tablets, watches, computers and media devices that share phone brands;
  - Samsung model codes for phones and for tablets;
  - Android's legacy default;
  - missing and meaningless hostnames;
  - the MAC gate, including multicast;
  - configured extra and ignored hostnames, including precedence and case-insensitivity;
  - determinism.

  Only made-up hostnames and MAC addresses; never real household ones (the repo is public).
- **PresenceRetriever.refresh(now)** with a fake PiholeSource returning device lists. It covers:
  - Someone Is Home and No One Is Home at the Away Delay boundary (29:59, 30:00, 30:01);
  - opening and closing Presence Periods, and an unchanged state doing nothing;
  - a restart continuing the open period;
  - a source error giving Presence Unknown;
  - the signal-lost alert, reminder, restored message and quiet hours (fake notifier and fake clock, like the Hive data-lost watchdog tests);
  - the 03:05 warning, present when the house is empty and absent otherwise.
- **HeatingRetriever.refresh(now)** with the existing fake HiveSource extended with the write verbs, recording calls. It covers:
  - every row of the control-policy table, including a boost skip and a hand change reverted;
  - a failed restore retried at the next poll;
  - dry-run recording without calls;
  - `off` making no calls and recording nothing;
  - a failed write stored as `failed` and not raised.

  For part A it also covers:
  - building the week (made-up 05:40–06:25 slots), rounding to the grain, a midnight end and a day without a plan slot;
  - writing only on a difference;
  - writing before restore on return;
  - `not_verified` when the read-back differs.
- **ComfortDayJob.refresh** and **RoomForecastJob.refresh**, through their existing seams, seeded with `presence_period` rows. They cover:
  - below-comfort minutes excluded inside `no_one_home` only;
  - the current scenario holding the latest target while a `no_one_home` period is open, and the schedule otherwise.
- **Schema Sync tests** for both new tables, and settings tests for the new config (defaults, absent section, refused values), in the shape of the existing schema-sync and config tests.
- **Prior art:** test_heating_retrieval (fake HiveSource over real MariaDB), test_hive_data_watchdog and test_ntfy_data_notifier (alerts and quiet hours), test_comfort_day_job and test_room_forecast_job (seeded octopus-app jobs), test_hive_config_settings, the `*_schema_sync` tests, test_grafana_dashboards and test_grafana_queries_document.
- **Live verification, not unit tests:** the real Pi-hole client and Hive writes are checked by a dry-run week compared against actual departures and returns before `control: on`. The schedule write is proven by the spike, then by a `comfort_schedule: dry_run` period before `on`.

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

- **What the design was based on (2026-11-17).** Over 24 hours, the two busiest household phones never went more than 10 minutes between DNS lookups at home; the quietest went up to 43 minutes overnight. One phone's departure and another's arrival were both clear to the minute. The household chose a 30-minute Away Delay knowing the quietest phone may occasionally read as away overnight. That is harmless when the schedule isn't heating, and it can be lengthened in config.
- Phones with a randomised MAC may rotate it. Hostnames survive rotation, which is why identity rests on them. Some phones send no hostname: iPhones with a private address sometimes, and Android 15 has a "Send device name" switch. Such phones are invisible unless named in config, and the 03:05 warning is how that is noticed.
- Never commit real hostnames, MAC addresses, the Pi-hole password or the real Hive schedule. Specs, tests and issues use made-up names and the made-up 05:40–06:25 at 19 °C slot.
- Rollout order: deploy with `control: dry_run`, compare a week of `heating_control_action` rows with reality, then `on`. Part A follows the same path after its spike.
- Glossary terms introduced in the design session: Phone, Phone Rule, No One Is Home / Someone Is Home, Presence Unknown, Presence Period, Away Override, Away Temperature, Comfort Schedule. Presence-Based Heating Control, Comfort Plan and hive-app have been updated.

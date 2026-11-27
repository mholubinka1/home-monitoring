# The Comfort Temperature steps half a degree on Held Boosts and Quiet Days

## Problem Statement

The Comfort Plan holds the room at the Comfort Temperature, which is learned as the weighted median room temperature at Boost Starts (ADR-0034). That worked while the room drifted freely. Since the Comfort Schedule began holding the room at that temperature (2026-11-24), every boost in the Comfort Window starts at or above it, so each one can only push the median up. A household that is comfortable presses nothing, and nothing in the house says "this is warmer than we need". The value can rise and never fall. The plan also rounds it up to Hive's half degree, which adds a small lift of its own. The household wants the lowest cost that still keeps them comfortable, and a value that can only climb works against that.

## Solution

The Comfort Temperature is no longer learned from boost-start temperatures. It moves in half-degree steps (ADR-0040):

- **Up 0.5 °C after a Held Boost**: a boost started inside the Comfort Window while Hive was holding the Comfort Temperature and the room was no more than 0.5 °C below it, with no upper limit (at 17.0, a room of 16.5 or warmer; [feature-held-boost-no-upper-limit](feature-held-boost-no-upper-limit.md)). The raise takes effect at once: the rest of the day's window is held warmer from the next hourly plan. A day gives at most one raise.
- **Down 0.5 °C after 3 Quiet Days** since the last step. A Quiet Day is one on which, inside the Comfort Window, someone was home for at least 2 hours while Hive held the Comfort Temperature, the heating ran, and nobody started a boost. The step applies from the next day.
- **Days that are neither, and boosts that are not held, count for nothing.** They neither add to the count nor reset it. That includes days away, Hive outages, dry runs, the Away Override, and boosts during a late warm-up.
- **Never below 16 °C.** This floor is hardcoded and deliberately not configurable. There is no ceiling.
- **Where it starts.** It carries on from the newest stored value rounded up to a half degree, never below the floor (16.8 becomes 17.0 on the Pi, which the house holds today). A fresh install with nothing stored starts at the 16 °C floor.

The stepped value replaces the median everywhere: the Comfort Plan, the dashboard's comfort line, and the minutes below comfort. There are no notifications. The dashboard line shows each step, and each day's stored row records what kind of day it was and the running count of Quiet Days.

## User Stories

1. As the household, I want the target to come down half a degree after 3 Quiet Days, so that the house finds the lowest temperature I'm content with and costs less.
2. As the household, I want a boost while the house is holding the target to raise it half a degree straight away, so that I'm warmer for the rest of the day and from then on.
3. As the household, I want at most one raise a day, so that several boosts in one cold spell don't run the target up.
4. As the household, I want a boost while the room is still warming up (more than 0.5 °C below the target) not to raise the target, so that a late morning warm-up isn't read as "too cold".
5. As the household, I want boosts outside the Comfort Window not to raise the target, so that the target only answers for the hours the plan holds.
6. As the household, I want a day only to count as quiet when someone was home for at least 2 hours of the window while the house held the target, and the heating ran, so that an empty or unheated house is never taken as evidence that the target is warm enough.
7. As the household, I want days with no evidence, and boosts that weren't held, neither to add to nor reset the count of Quiet Days, so that a day away or a warm-up boost doesn't stall or rush a step.
8. As the household, I want nothing to move while the Comfort Schedule isn't actually holding the room (a dry run, Hive offline, a manual change, the Away Override), so that only real evidence moves the target.
9. As the household, I want the target never below 16 °C, so that the house is never held in the range linked to damp and health problems; I want that floor fixed in code, not a setting.
10. As the household, I want no ceiling, so that the target can follow me up if I keep boosting.
11. As the household, I want the change to carry on from today's held 17.0 °C, so that deploying it changes nothing in the house on day one.
12. As a new user of the repository, I want a fresh install to start at the 16 °C floor, so that it has a starting value without the boost history the old median needed.
13. As the household, I want the dashboard's comfort line to step when the target moves, including a raise part-way through a day, so that I can see what the house aimed for and when it changed.
14. As the household, I want minutes below comfort counted against the target in force at each reading, so that the figure matches what the house was asked to hold.
15. As the household, I want each day's row to say whether it was quiet, had a Held Boost, or neither, and the running count of Quiet Days, so that I can see why the target moved.
16. As a maintainer, I want days settled before the change kept as stored, so that past figures never change.
17. As a maintainer, I want the boost-median code removed, so that only one Comfort Temperature rule exists.
18. As a maintainer, I want the glossary, ADRs, queries document and explainer to describe the new rule, so that it can be understood and challenged later.

## Implementation Decisions

**Comfort module (octopus-app `data/comfort`).** The boost-median learning is deleted: `boost_weight`, `heating_days`, `comfort_temperature`, the weighted median and their constants (`BOOST_WINDOW_DAYS`, `HALF_LIFE_HEATING_DAYS`, `MIN_BOOST_STARTS`). `boost_starts`, `counted_hours` and `BOOST_GAP` stay. New pure functions and constants, in the style of the existing ones:

- `COMFORT_FLOOR = 16.0`, with a comment that it is deliberately hardcoded and not configurable (ADR-0040). Also `COMFORT_STEP = 0.5`, `QUIET_DAYS_PER_STEP = 3`, `HELD_MARGIN = 0.5` (how far below the target the room may be for a boost to count as held, and how far below the value in force a reading must be to count as minutes below; see feature-comfort-held-margin) and `QUIET_MINUTES = 120`.
- `round_up_to_half` moves here from the room forecast module, its only remaining use being the seed.
- **A held poll** is one whose Hive target equals the Comfort Temperature in force at that poll, compared in half degrees, and that is not offline (`online = False`). While Hive cannot reach the thermostat it repeats stale values, so an offline poll is never evidence.
- **A Held Boost** is a Boost Start that meets three conditions. It is inside the day's Comfort Window. The poll just before it was held and no more than `BOOST_GAP` earlier. Its room temperature is at least the target minus `HELD_MARGIN`, with no upper limit ([feature-held-boost-no-upper-limit](feature-held-boost-no-upper-limit.md)). Only the first Held Boost of a day counts, and its time becomes the day's `raised_at`.
- **A Quiet Day** is a day with no Boost Start inside the Comfort Window (a Held Boost raises; any other boost there makes the day count for nothing), on which three things hold. Inside the Comfort Window, `POLL_MINUTES ×` the polls that were held and outside a No One Is Home period come to at least `QUIET_MINUTES`. At least one online poll in the window had the heating on (`state = 'ON'` or boost active, ADR-0033). And the day is settled; the provisional row for today may also show `quiet`, but the step itself is only applied when the next day's row is written.
- **The Comfort Window** for a day comes from the existing `comfort_window(windows, day)` in the room forecast module.
- **Each day starts from the previous stored row** (the newest earlier day with a `comfort_temp`), using this rule from the design:

```text
previous row missing                      -> start 16.0 (the floor), count 0
previous row has quiet_count NULL         -> start max(16.0, round_up_to_half(comfort_temp)), count 0   # settled before this change
otherwise end = comfort_temp + (0.5 if raised_at else 0)
    previous quiet_count >= 3             -> start max(16.0, end - 0.5), count 0
    else                                  -> start end, count previous quiet_count
day's quiet_count = 0 after a Held Boost, count + 1 on a Quiet Day, else count unchanged
```

- **The value in force at a moment** is the day's `comfort_temp` before `raised_at` and `comfort_temp + 0.5` from it on.
- `minutes_below` counts a poll only when the room is more than `HELD_MARGIN` below the value in force at that poll (narrowed by [feature-comfort-held-margin](feature-comfort-held-margin.md)), in counted hours only and not during No One Is Home, and leaves out offline polls, whose stale room reading would otherwise count for the whole outage.

**Schema (additive, ADR-0005).** `comfort_day` gains three nullable columns, added by Schema Sync:

- `raised_at` (DateTime, naive UTC): when the day's Held Boost started. NULL means no raise.
- `day_kind` (String(20)): `quiet`, `held_boost`, or NULL for neither.
- `quiet_count` (Integer): Quiet Days since the last step, after this day. NULL marks a row settled before this change.

Existing columns change meaning from the first stepped day on, and the meanings go in the model's comments:

- `comfort_temp` is the value at the start of the day.
- `boost_starts` (NOT NULL, and Schema Sync can't relax it) is the number of Boost Starts on the day itself, counted whether or not the first reading has a room temperature.
- `counted_hours`, `minutes_below` and `computed_at` are unchanged.

`ComfortDayRecord` gains the three fields, with defaults of None.

**Comfort day job (`ComfortDayJob`).**

- The constructor takes the Comfort Windows (`ComfortDayJob(mariadb, comfort_windows)`), and `main` passes `settings.comfort_windows`, as it already does for `RoomForecastJob`.
- Which days a run writes, and the settling rule, are unchanged. Today's row is rewritten every hour, so a raise part-way through the day is today's row gaining `raised_at`. A finished day is written one last time on the first run after it ends, and older days never.
- State carries day to day within a run (the previous row's start value, raise and count), as `carried` does today. The first day of a run reads its predecessor from the database.
- Each day's polls are read once, from `BOOST_GAP` before the day's start, so a boost running across midnight isn't taken for a new one. Only polls inside the day are used for anything else.
- The 730-day read of boost polls is dropped. The 365-day read of heated hours stays, for Counted Hours only.

**MariaDB client.** These changes cover the reads:

- `read_room_temperatures` is replaced by a read of each poll's `(polled_at, current_temp, target_temp, state, boost_active, online)` in a UTC range; octopus-app's mirror of hive-app's `heating_status` gains the `online` column hive-app already writes. Like the existing reads, a missing table reads as no rows.
- `read_boost_polls` is removed.
- `read_latest_comfort_temperature` is replaced by a read of the newest `comfort_day` row before a date that has a `comfort_temp`, returned as a `ComfortDayRecord`. The comfort job uses it for a run's first predecessor, and the room forecast job (with the date set to tomorrow) for today's row.

**Room forecast job.** `_store_plan` reads the newest comfort row up to today and plans for the value in force at the end of what is known: `comfort_temp`, plus 0.5 when `raised_at` is set. A pre-change row is rounded up to a half degree, never below the floor, through the same comfort-module helper. The plan no longer rounds a stepped value. With no row at all, the plan isn't stored, as today. Today and tomorrow get the same value. A step down for tomorrow appears after midnight, on the first hourly run that writes the new day's row, which is before the window opens. hive-app is unchanged: it writes whatever plan slots it finds.

**Scheduling.** Unchanged. The comfort job runs hourly after a successful heating-interval run and before the room forecast. So a raise reaches Hive within about an hour, and the boost itself warms the room in the meantime.

**Dashboard (`data/grafana/gas-heating-dashboard.json`).**

- Panel 1's query D (comfort) emits a point at each day's London midnight with `comfort_temp`, a point at `raised_at` with `comfort_temp + 0.5` when it is set, and the existing closing point carrying the value in force at the end of the newest day. The series stays drawn `stepAfter`. The panel description says the line is the stepped Comfort Temperature.
- Panel 4's query is unchanged. Its description drops "fewer than 5 boosts" and says the minutes are counted against the target in force.
- Re-export the live dashboard before applying, and deploy octopus-app before the import, as for any panel that reads a new column.

**Docs.**

- `data/grafana/mariadb/gas-heating-queries.md`: the `comfort_day` schema line gains the new columns, and the query D and panel 4 text are updated, linking ADR-0040.
- `docs/heating-model.md`: section 11 is rewritten in plain language with a worked example, and the plan section and glossary entries drop "rounded up to a half degree".
- `.agent-docs/specs/feature-comfort-temperature-model.md` gets a note at the top that ADR-0040 superseded its learning rule.
- ADR-0040, ADR-0034's status line and the glossary (Comfort Temperature, Held Boost, Quiet Day) are already written.

## Testing Decisions

Tests check behaviour at the highest seam, through stored rows; there are no unit tests of the pure functions.

- **`ComfortDayJob.refresh(now)`** against the SQLite `mariadb_client` fixture, as in `tests/test_comfort_day_job.py`. Tests seed `heating_status` polls (with `target_temp`, `state`, `boost_active`), `presence_period` rows, `heating_interval` rows and earlier `comfort_day` rows, run the job with a fixed clock, and assert the rows. One scenario per behaviour:
  - a Held Boost raises the day by 0.5 from its time (`raised_at`, `day_kind = held_boost`, count 0), and the next day starts at the raised value;
  - a second Held Boost the same day does not raise again;
  - a boost with the room more than 0.5 below the target is not held, and leaves the count unchanged;
  - a boost outside the Comfort Window is not held;
  - a boost whose previous poll had a different Hive target (dry run, Away Override, manual change) is not held;
  - 3 Quiet Days step the next day down 0.5 and reset the count;
  - a day with under 2 hours held at home in the window is not quiet and leaves the count unchanged;
  - a day with the heating never on in the window is not quiet;
  - time inside a No One Is Home period doesn't count towards the 2 hours;
  - at the 16 °C floor, 3 Quiet Days leave it at 16 and reset the count;
  - the first stepped day after a pre-change row starts from that row rounded up to a half degree, and with no earlier row it starts at 16;
  - minutes below compare each poll with the value in force, before and after a raise;
  - `boost_starts` is the day's own count;
  - settling still holds: today rewritten, yesterday settled once, older days never;
  - a missing `heating_status` table writes nothing and does not raise.

  Existing scenarios about the median, weights, the 5-boost minimum and the two-year window are removed. Counted-hours and settling scenarios are kept, adjusted to the new seed.
- **`RoomForecastJob.refresh(now)`** in the existing room-forecast-job tests: the plan's slots hold a stored stepped value as it is (17.5 stays 17.5), hold +0.5 when today's row has `raised_at`, and round a pre-change value up (16.8 becomes 17.0).
- **Dashboard and queries document.** `tests/test_grafana_dashboards.py` and `tests/test_grafana_queries_document.py` are updated so the comfort query includes the raise point and the document's copy matches the dashboard. Panel SQL is checked once, in the final dashboard review, on a throwaway database copy, never on the live MariaDB.
- **Wiring.** `tests/test_refresh_scheduling.py` and any constructor call sites are updated for the new `ComfortDayJob` argument.

## Out of Scope

- A ceiling on the Comfort Temperature (declined by the household).
- Notifications when it moves.
- A manual override of the Comfort Temperature; boost covers a one-off.
- A separate Comfort Temperature for each part of the day or season.
- A model of the chance of a boost at each temperature with a cost trade-off, and treating a manual turn-down as a "too warm" signal (ADR-0040 records both as possible next steps).
- Running the comfort job or plan more often than hourly.
- Changes to hive-app, to the Comfort Window, the Away Temperature, presence, or Counted Hours.
- Restating days settled before the change.

## Further Notes

- With 3 Quiet Days per step, the staircase settles where a day passes without a Held Boost with probability 0.5^(1/3) ≈ 0.79: a boost on about one day in five.
- A Hive boost lasts 30 to 60 minutes and lifts the room about 0.5 °C, so a raise landing within the hour mostly arrives while the boost is still heating.
- Since go-live the held room has stayed within about 0.2 °C of the target, so `HELD_MARGIN = 0.5` comfortably separates "held" from "still warming".
- Worked examples in the repository use made-up values (for example a target of 17.0 and a boost at 16.7), never the household's real schedule times.
- As before, a wrongly settled day is repaired by deleting its row and every later row by hand; the next run refills them from the newest remaining row.

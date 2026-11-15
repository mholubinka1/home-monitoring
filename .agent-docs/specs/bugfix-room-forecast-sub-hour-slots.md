# Room forecast follows slots, windows and the newest reading to the minute

## Problem Statement

The room forecast (E3, [ADR-0035](../adr/0035-room-forecast-and-comfort-plan-come-from-a-small-learned-thermal-model.md)) works in whole hours, and three of its shortcuts show on the household's real data, first seen on 2026-11-14, the day it was deployed:

- **Current schedule.** An hour takes the Hive slot target in force at the hour's start. A slot shorter than an hour that straddles an hour boundary is therefore forecast as one whole hour of heating in the later hour. Measured over a week, the thermostat ran for the slot's own minutes, split across the two hours. The forecast showed nothing in the first hour and 60 minutes in the second: up to an hour late, and too much heating and cost.
- **Comfort plan.** A window that opens inside an hour is reached by interpolating within that hour, so the last pre-heat hour's minutes can fall up to 30 minutes after the window has opened, and the target is held only from the next whole hour. A window that closes inside an hour leaves that hour unheated.
- **First hour.** The job runs some minutes past the hour, but treats the newest reading as the room at the top of that hour and forecasts the whole hour. Heating that happened before the reading is counted again: after a slot ends, the live line sits up to about 1.2 C too warm until the next run.

## Solution

The forecast still stores one row per hour, but inside an hour it now follows the minute at which each target starts and stops:

- Under the current schedule, each hour heats only during the minutes its Hive slots call for heat.
- Under the comfort plan, the target is held from the window's opening minute to its closing minute, and the pre-heat ends exactly when the window opens.
- The first hour is forecast only from the newest reading's time onwards.

A schedule or window that sits on whole hours gives the same numbers as before.

Terms are in `.agent-docs/context.md`.

## User Stories

1. As the household, I want the current-schedule forecast to heat only for the minutes each Hive slot is in force, so that the heating minutes, the cost and the time the room warms match what the thermostat does.
2. As the household, I want the comfort plan to hold the target from the minute the window opens to the minute it closes, so that the plan is comfortable for the whole window and for nothing outside it.
3. As the household, I want the plan's pre-heat to end exactly when the window opens, so that no pre-heat minute is counted after the room should already be warm.
4. As the household, I want the first hour forecast only from the newest reading onwards, so that heating that has already happened is never counted twice.
5. As the household, I want a schedule or window on whole hours to forecast exactly as before, so that the change alters only what was wrong.
6. As a maintainer, I want one mechanism for time inside an hour, shared by the current schedule, the plan and the first hour, so that there is one rule to understand and test.
7. As a maintainer, I want the heating guide's worked example and its stated limits to match the new rules, so that the guide stays true.

## Implementation Decisions

**Modules changed** (octopus-app): `data/room_forecast.py` (the maths), `data/room_forecast_job.py` (the first hour and the parts for each scenario), their tests, and `docs/heating-model.md` section 12. No table, config, dashboard or hive-app change. This spec replaces three rules of the E3 spec (`feature-hourly-room-forecast-comfort-plan-e3.md`): "the target of the slot in force at the hour's start", "the room at a start that falls inside an hour is read by linear interpolation", and "an end time inside an hour leaves the hour it falls in unheated".

**Parts of an hour.** A forecast hour is a list of **parts**, in time order. Each part has a share of the hour and a target, or no target. A new part starts wherever a target changes inside the hour. An hour with no change inside it is one part with a share of 1.

**Stepping an hour.** The thermal model stays hourly and is not refitted.

- The hour's drift without heating is computed once, from the room at the start of the hour (for the first hour, from the reading): `P = loss_per_degree * (outdoor - room) + carried_gain * previous heating fraction + sun_gain * radiation + constant_gain`.
- The drift is spread evenly over the hour. For each part in order, with share `s` and target `T`: the heating is `h = clamp((T - room - s * P) / heating_gain, 0, s)` (0 with no target), and the room becomes `room + s * P + heating_gain * h`.
- The hour's heating fraction is the sum of `h` over its parts; the stored `on_minutes` is that fraction times 60, rounded. That sum is the "previous heating fraction" of the next hour.
- With one part this is exactly the existing `hold_target`, so whole-hour results do not change. Summed over the parts, the room's change equals the hourly model's: `P + heating_gain * fraction`.

Averaging the targets over the hour was rejected: a half hour at 7 C and a half at 19 C averages to 13 C, below the room, and would forecast no heating.

**Current schedule.** The parts of an hour come from the Weekly Schedule's slot start times that fall inside it and change the target, in local time (a slot that repeats the target in force is not a new part: a part heats the least that reaches its target by its own end, so a boundary where nothing changes would force heating forward and the hour would no longer match its whole-hour result); the first part's target is the slot in force at the hour's start (including the previous day's last slot before the day's first, as today). Mode OFF is one part with no target. A slot is assumed to start heating at its start time: the measured week shows no early start.

Worked example (model numbers of the tests: loss 0.02, heating gain 1.3, carried 0.3, constant 0.08; 10 C outside, no sun): a slot at 19 C from 05:40 to 06:25, room 16.0 C at 05:00. The 05:00 hour heats 20 minutes (05:40 to 06:00) and ends at 16.39 C. The 06:00 hour heats 25 minutes (06:00 to 06:25) and ends at 16.99 C. Before this change: nothing in the 05:00 hour and 60 minutes in the 06:00 hour.

**Comfort plan.** For each Comfort Window in time order (including one that began on the previous local day and is still open), the plan's target is held from `opening - lead` to the closing minute, as parts like any other. The **lead** is the shortest that has the room at the target at the opening minute, found by bisection as today: zero when the room is already there, and capped at the time between the forecast's start and the opening when the target is out of reach. A window whose opening is before the forecast's start, or beyond the hours with weather, has no lead. Holding the target from `opening - lead` is the same as heating fully until the opening, because the room is below the target throughout the lead. This removes the separate pre-heat code, the interpolation and the unheated closing hour: a window closing at 23:30 holds the target for the first half of the 23:00 hour and nothing after. Overnight windows and the weekday and weekend windows behave as before.

**First hour.** The forecast starts at the newest reading's time, not at the top of the hour. The first forecast hour is the hour containing the reading; only the parts after the reading are stepped, and their shares add up to the rest of the hour. Its stored row keeps the hour's start as `hour_start`, the reading as `room_temp`, and the heating still to come in that hour as `on_minutes`. The heating fraction carried into the second hour is the heating already seen in the first hour before the reading (thermostat polls that were on, at hive-app's 2-minute interval, capped at the elapsed minutes) plus the forecast remainder. The first hour's own carried term still comes from the stored `heating_hour` of the hour before, as today. The 30-minute limit on the reading's age is unchanged. Both scenarios start this way.

**Unchanged.** The stale-data guards, the row fields, the day-ahead rows (tomorrow's hours are always whole), the cost arithmetic, the weather stamp rule, and local wall-clock arithmetic on clock-change days.

**Docs.** `docs/heating-model.md` section 12: the worked example becomes a half hour of pre-heat ending at 06:30 (18.9 C at 06:00, 6 C outside: half the hour's drift of -0.2 C plus half an hour of heating at 1.4 C per hour gives +0.6 C, so 19.5 C at 06:30), and the text says the target is held from the opening minute and that MANUAL mode is forecast as if it were SCHEDULE.

**Deploy.** Recreate octopus-app on the Pi. Check the next morning with light read-only queries: the live forecast's heating minutes for the hours a slot straddles should match the `heating_hour` rows the thermostat then produces, within a poll or two.

## Testing Decisions

Two existing seams, agreed with the household. Tests assert stored rows and returned values, not internals, and use a made-up schedule (a slot from 05:40 to 06:25), never the household's real slot times: this repository is public.

1. **The maths**, in `tests/test_room_forecast.py`: an hour stepped as parts, the schedule's parts for an hour, the plan's lead search and window edges. Tests that encode the replaced rules (the interpolated pre-heat fractions, the unheated closing hour, the hour-start weekend comparison) are rewritten to the new rules with hand-computed values.
2. **The job**, `RoomForecastJob.refresh(now)` on the SQLite fixture, in `tests/test_room_forecast_job.py` (and a new file if it nears pylint's 1000-line limit): stored minutes, cost and the first hour.

Scenarios (each also a BDD red-green step):

- a slot that straddles an hour boundary heats its own minutes in each hour (20 and 25), and cost and kWh follow;
- a room that reaches a slot's target part-way through stops heating there;
- two slot changes inside one hour each heat for at most their own length;
- a schedule whose slots start on the hour stores the same rows as before, and mode OFF still stores no heating;
- a slot that repeats the target in force does not start a new part, so the hour keeps its whole-hour result;
- the plan's pre-heat ends at the opening minute, with the room at the target then, and no pre-heat minute after it;
- the plan holds the target from the opening minute, in the same hour;
- a window closing inside an hour holds until that minute and heats nothing after;
- a room already at the target at the opening has no lead, and a target out of reach heats from the forecast's start;
- a window on whole hours, an overnight window after midnight, and the weekday and weekend windows behave as before;
- a reading taken after a slot has ended stores no heating for the rest of that hour;
- a reading taken inside a slot stores only the minutes from the reading to the slot's end or the hour's end;
- heating seen before the reading in the first hour is carried into the second hour with the forecast remainder;
- a reading in the previous hour starts the rows at that hour, with only its remaining minutes;
- the first row's room is the reading, for both scenarios.

## Out of Scope

- MANUAL mode: the current-schedule forecast still follows the slots when Hive is holding a manually set temperature. Recorded as a follow-up.
- Finer time steps throughout, or refitting the thermal model at a finer resolution.
- A boost running at forecast time (still ignored, ADR-0035), and Hive features that start a slot early.
- Clock-change days beyond the existing local wall-clock arithmetic.
- Any table, config or dashboard change, and any write to Hive.

## Further Notes

- Evidence (2026-11-09 to 2026-11-14, `heating_hour`): on every day but one the heating ran for the slot's own minutes, split across the two hours it straddles; the forecast of 2026-11-14 put a full hour in the later one.
- The stored `room_temp` of the first row is the room at the reading's time, drawn at the hour's start, as before.
- The default comfort windows open inside an hour (06:30 and 07:45), so plan rows change for the defaults: less pre-heat is stored after the opening and the hold starts at the opening minute.
- The lead search steps the forecast from its start for each trial; the horizon is at most 48 hours and a few parts per hour, so the cost is negligible.

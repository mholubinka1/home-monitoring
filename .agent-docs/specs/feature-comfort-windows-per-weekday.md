# Comfort Windows are set per weekday and may run past midnight

## Problem Statement

The Comfort Plan holds the room from 07:30 to 23:00 on weekdays and from 08:30 to midnight at weekends. The household goes to bed later on the nights before a weekend day and earlier on the night before a workday. So Friday's window ends too early and Sunday's too late, and a window can't run past midnight even where the household wants it to.

## Solution

The Comfort Windows are configured per weekday, and a window may end the next morning (ADR-0041). The defaults are:

| Day | Window |
| --- | --- |
| Monday to Thursday | 07:30 to 23:00 |
| Friday | 07:30 to 00:30 Saturday |
| Saturday | 08:30 to 00:30 Sunday |
| Sunday | 08:30 to 23:00 |

- The plan holds the Comfort Temperature through 00:30 on Saturday and Sunday mornings, and Hive is written to match.
- The part of a window after midnight is evidence for the day the window opened on. A boost at 00:15 on Saturday morning can be Friday's Held Boost, and held time then counts towards Friday's Quiet Day. Friday settles on the first hourly run after 00:30.
- Minutes below comfort and the day's own count of Boost Starts stay on calendar days.
- The schedule table shows Friday's plan slot whole (for example `06:50 - 00:30`).

## User Stories

1. As the household, I want the windows to run 07:30 to 23:00 Monday to Thursday, 07:30 to 00:30 on Friday, 08:30 to 00:30 on Saturday and 08:30 to 23:00 on Sunday by default, so that the house is warm until we go to bed and no later.
2. As the household, I want to set each weekday's window in the config, with an end at or before the start meaning the next morning, so that I can change any evening without code.
3. As a maintainer, I want the old `weekday` and `weekend` config keys refused at startup with a message naming them, so that an old config is never silently replaced by the defaults.
4. As the household, I want Hive to hold the comfort temperature until 00:30 on Saturday and Sunday mornings and then drop to the Away Temperature, so that the late evenings are warm.
5. As the household, I want a boost between midnight and 00:30 to count as evidence for the evening before, so that a cold late evening can raise the comfort temperature.
6. As the household, I want held time and the heating after midnight to count towards the evening's Quiet Day, and a boost then to stop it being quiet, so that the whole evening is judged as one.
7. As a maintainer, I want a day settled only after its window closes, so that the after-midnight evidence is never missed.
8. As the household, I want the schedule table to show Friday's plan slot as one slot ending at 00:30, so that it reads as I think of it.
9. As a maintainer, I want the README, config template and explainer to describe the per-weekday windows, so that the config can be set from the docs.

## Implementation Decisions

**Config (octopus-app `common/config`).** `ComfortWindows` has seven `ComfortWindow` fields, `monday` to `sunday`, with the defaults in the table. Each window keeps today's validation: quoted clock times, rounding earlier to 2-minute marks, and refusal of a window whose different times round to the same mark. A day left out of the config takes its default. Unknown keys are refused (`extra="forbid"`), so the old `weekday`/`weekend` keys stop startup with a validation error naming them. `ComfortWindow`'s docstring says an end at or before the start is the next morning, which `comfort_window` already does.

**Plan (octopus-app `data/room_forecast`).** `comfort_window(windows, day)` returns the named weekday's window, with an end at or before the start on the next day, as now. `_held_spans` already includes yesterday's window while it is still open, so the plan needs no other change.

**Plan slots (`RoomForecastJob._store_plan_slots`).** Each slot keeps the day its window opened on and its true `ends_at` (Saturday 00:30 for Friday). Today the job keeps a passed slot of today or tomorrow until the day rolls over. It now also keeps yesterday's stored slot for the whole of today, so hive-app can still read Friday's slot all through Saturday.

**hive-app (`data/heating` and `data/comfort_schedule`).**

- `HeatingRetriever._build_week` reads the plan slots for yesterday, today and tomorrow.
- `build_week` writes each written day (today and tomorrow) from every slot that overlaps it:
  - **The slot's own day:** the target at the start minute. Then the Away Temperature at the end minute when the slot ends on its day, or at 23:59 when it ends at or after the next midnight.
  - **The next day, for a slot ending after the next midnight:** the target at minute 0 and the Away Temperature at the end minute, before the next day's own slots.
- A slot that does not start on its day is still refused, with its reason. A slot ending past the next midnight is no longer refused.
- A day with no plan slot of its own keeps Hive's entries, even when yesterday's slot runs into it: writing the tail alone would hold the Away Temperature for the rest of that day (for example after a missed plan run).
- `validate_week` is unchanged (an entry at minute 0 is already allowed).

**Comfort job (octopus-app `data/comfort_job` and `data/comfort`).**

- **Polls:** each day's polls are read from `BOOST_GAP` before its start to the later of its end and its window's close.
- **Staircase evidence:** the Held Boost and Quiet Day tests use the whole window, including the tail, and the No One Is Home periods up to the window's close, so leaving after midnight stops the tail counting as held at home.
- **Calendar-day figures:** minutes below comfort and the day's `boost_starts` count only polls inside the calendar day.
- **Settling:** a day is settled once written at or after the later of its next midnight and its window's close. Each run starts from the oldest day not yet settled, so Friday is rewritten, and settles, before Saturday.

**Docs.** Update the README's `comfort_windows` paragraph, `deployments/octopus-app/config.yml.template`, and the comfort-window sentences in `docs/heating-model.md`. The glossary's Comfort Window entry and ADR-0041 are already written.

## Testing Decisions

Seams already used for each module, asserting behaviour:

- **`get_settings`** (`tests/test_config_settings.py`): the defaults per weekday, a partial override keeping the other days' defaults, and old `weekday`/`weekend` keys refused with a message naming the key.
- **`RoomForecastJob.refresh(now)`** (`tests/test_room_forecast_job_schedule_slots.py`): Friday's stored plan slot ends at 00:30 Saturday, Sunday's at 23:00, and Friday's slot is still stored during Saturday.
- **`HeatingRetriever.refresh(now)`** (`apps/hive-app/tests/test_comfort_schedule_write.py`):
  - On a Friday, Friday is written with the target at the pre-heat and Away at 23:59, and Saturday with the target at 00:00, Away at 00:30 and then its own slot.
  - On a Sunday, Sunday keeps Saturday's 00:00 to 00:30 tail.
  - The two existing tests that pin the refusal of a slot ending past midnight are replaced by these. The refusal of a slot that starts before its day stays.
- **`ComfortDayJob.refresh(now)`** (`tests/test_comfort_day_job.py`):
  - A Held Boost at 00:15 Saturday raises Friday, and Saturday then starts raised.
  - Friday is not settled by the 00:04 run but is by the first run after 00:30.
  - Held time between 00:00 and 00:30 counts towards Friday's Quiet Day.
  - Saturday's minutes below and Boost Starts still count Saturday's 00:00 to 00:30 polls.
- **Fixtures:** the shared `PINNED_WINDOWS` test fixture is rebuilt for the seven-day shape.

## Out of Scope

- Bank holidays.
- A window longer than 24 hours, or one whose tail overlaps the next day's own window.
- Any change to the staircase rules, the plan's pre-heat, or the dashboard query.

## Further Notes

- On Friday and Saturday nights Hive drops to the Away Temperature for one minute at 23:59. The room does not notice.
- UK clocks change at 01:00 or 02:00, so a 00:30 close is never moved by a clock change.
- The Pi's octopus-app config does not set `comfort_windows`, so the new defaults take effect on deploy.
- Worked examples in the repository use made-up values, never the household's real schedule times. The window defaults are configuration, already public.

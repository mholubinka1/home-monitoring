# Default comfort windows: 07:30 to 23:00 on weekdays, 08:30 to midnight at weekends

## Problem Statement

The comfort plan's built-in windows (06:30 to midnight on weekdays, 07:44 to midnight at weekends) no longer match when the household wants the house comfortable, so the plan pre-heats too early and holds the comfort temperature too late on weekdays.

## Solution

Change the defaults of the optional `comfort_windows` setting to 07:30 to 23:00 on weekdays and 08:30 to midnight (`00:00`) at weekends. A `comfort_windows` section in `config.yml` still overrides them, as before.

## User Stories

1. As the household, I want the comfort plan to hold comfort from 07:30 to 23:00 on weekdays when I set nothing, so that the pre-heat starts for 07:30 and stops at 23:00.
2. As the household, I want the weekend window to be 08:30 to midnight by default, so that weekend mornings start later.
3. As a maintainer, I want the forecast tests to pin their own windows, so that changing the defaults again does not move their expected times.
4. As a maintainer, I want the README, config template, heating guide and glossary to state the new defaults.

## Implementation Decisions

- `ComfortWindows` defaults in `apps/octopus-app/octopus_app/common/config.py`: weekday `07:30`-`23:00`, weekend `08:30`-`00:00`. Both are even minutes, so nothing is rounded.
- No schema, job or dashboard change. The Pi's `config.yml` sets no `comfort_windows`, so it picks up the new defaults when octopus-app is recreated.
- Forecast tests (`test_room_forecast*.py`) use `PINNED_WINDOWS` (the old 06:30 and 07:44 windows) so their numbers stand; the config tests assert the new defaults.
- ADR-0035 is left as written: it records the decision when it was made, and the glossary and heating guide carry the current values.

## Testing Decisions

The seam is `ApplicationSettings.comfort_windows`: one test asserts the defaults, and the existing override tests keep covering configured values. The full octopus-app suite must pass.

## Out of Scope

- Changing how the pre-heat lead is computed.
- Any other config default.

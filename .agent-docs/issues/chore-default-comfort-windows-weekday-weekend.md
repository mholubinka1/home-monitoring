# Issues: chore/default-comfort-windows-weekday-weekend

## Change the default comfort windows

**GitHub issue**: none (small change recorded locally only)

**Blocked by**: None

**User stories**: 1, 2, 3, 4

### What to build

Default `comfort_windows` become 07:30 to 23:00 on weekdays and 08:30 to midnight at weekends. The forecast tests pin their own windows, and the README, config template, heating guide and glossary state the new defaults.

### Acceptance criteria

- [x] Given a config with no comfort_windows, when the settings load, then weekdays are 07:30 to 23:00 and weekends 08:30 to 00:00.
- [x] Given a config that overrides one window, when the settings load, then the other keeps its new default.
- [x] Given the forecast tests, when the defaults change, then they still pass because they pin their own windows.
- [x] Given the README, config template, heating guide and glossary, when read, then they state the new defaults.

# Issues: feature-configurable-local-timezone

> Work complete — PR ready to merge.

Parent: [#663](https://github.com/mholubinka1/home-monitoring/issues/663). Spec: `.agent-docs/specs/feature-configurable-local-timezone.md`.

## TZ-1 · Shared local-day module and a configurable timezone in octopus-app — [#667](https://github.com/mholubinka1/home-monitoring/issues/667)

**Blocked by**: None

**User stories**: 1, 2, 3, 6

### What to build

Move octopus-app's `local_day` module into `libs/common` as the one shared local-day module, driven by an optional top-level `timezone` setting (default `Europe/London`) in octopus-app's `config.yml`. The module holds the active zone, set once at startup after the name is validated. Every octopus-app day bucket (consumption, summaries, cost forecast, completeness) uses it. x2r.uk's naive timestamps keep being read as London through a source-named constant, because that is the zone x2r documents. The octopus-app config template documents the setting.

### Acceptance criteria

- [x] With no `timezone` setting, every octopus-app day bucket matches today's `Europe/London` behaviour, including the 23- and 25-hour daylight-saving days (the existing tests pass unchanged).
- [x] With `timezone: America/New_York`, the shared module's day starts, local dates and expected half-hour counts (46/48/50) follow New York's days and DST, and octopus-app's consumption summaries bucket by New York days.
- [x] An unknown timezone name fails octopus-app at startup with a critical log naming `timezone`, and no job runs.
- [x] A naive x2r.uk timestamp is read as `Europe/London` whatever the configured timezone.
- [x] octopus-app's `local_day` module is gone; its call sites import the shared module; the active zone is reset between tests by a fixture.
- [x] The octopus-app config template documents `timezone`.

---

## TZ-2 · hive-app follows the configured timezone — [#668](https://github.com/mholubinka1/home-monitoring/issues/668)

**Blocked by**: #667

**User stories**: 1, 2, 3

### What to build

hive-app gets the same optional top-level `timezone` setting, validated and applied at startup with the shared module. Its weather completeness report, hour-stamp bucketing and "yesterday" rule use the shared module instead of the `LOCAL_TIMEZONE` constant, and the Open-Meteo forecast request's `timezone` parameter is the configured zone. The archive request stays in UTC. The hive-app config template documents the setting.

### Acceptance criteria

- [x] With no `timezone` setting, hive-app's completeness report and forecast request behave as today (the existing tests pass unchanged; the forecast request sends `Europe/London`).
- [x] With `timezone: America/New_York`, the completeness report expects 23/24/25 stamps per New York local day and the forecast request sends `America/New_York`.
- [x] An unknown timezone name fails hive-app at startup with a critical log naming `timezone`.
- [x] `LOCAL_TIMEZONE` and the forecast request's `Europe/London` literal are gone from hive-app.
- [x] The hive-app config template documents `timezone`.

---

## TZ-3 · Grafana follows the timezone; ADR, glossary and README describe it — [#669](https://github.com/mholubinka1/home-monitoring/issues/669)

**Blocked by**: None

**User stories**: 4, 5, 6

### What to build

The Grafana dashboard gets a constant `timezone` variable (default `Europe/London`) and every `'Europe/London'` literal in its queries, and in the standalone reference queries, becomes `'${timezone}'`. ADR-0010 gets a dated update, the glossary's Local Day entry and rule-describing "London day" wording say "local day", and the README documents the setting: both apps need the same value, and after changing zone on a populated database the `daily_consumption_summary` rows that have a reading count are deleted so the startup backfill rebuilds them (rows without a count cannot be rebuilt and stay in the old zone).

### Acceptance criteria

- [x] `dashboard.json` has a `timezone` template variable defaulting to `Europe/London`, and no query contains a literal `Europe/London`.
- [x] `queries.md` documents the `timezone` variable and uses `'${timezone}'` in its reference queries instead of the literal.
- [x] A test fails if `Europe/London` appears in the dashboard queries or `queries.md` outside the variable's default.
- [x] ADR-0010 has a dated update; the glossary describes the Local Day as a setting; the README describes the `timezone` setting, that both apps must match, and the rebuild step after a zone change.

---

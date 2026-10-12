# Configurable local-day timezone

## Problem Statement

Every "day" figure in the repo (consumption summaries, cost forecasts, the weather completeness report, the Grafana day panels) is bucketed in `Europe/London`, hard-coded in several places: octopus-app's `local_day` module, hive-app's `weather_backfill` and its forecast request, and about 20 Grafana queries. Anyone running this outside the UK, or just wanting the day rule to be a setting rather than a constant, has to edit code and SQL in many places and keep them agreeing by hand.

## Solution

One shared local-day module in `libs/common`, driven by an optional `timezone` setting in each app's `config.yml` that defaults to `Europe/London`, so existing deployments behave exactly as before. Both apps use it for every day bucket, hive-app's Open-Meteo forecast request uses the same zone, and the Grafana queries take the zone from a `timezone` dashboard variable instead of literals. An invalid zone fails at startup with a clear message.

## User Stories

1. As the account holder, I want to set my timezone once in each app's config, so that every day figure uses my local days.
2. As an existing user, I want no `timezone` setting to behave exactly as today, including the 23- and 25-hour daylight-saving days, so that upgrading changes nothing.
3. As an operator, I want an invalid timezone to stop startup with a message naming the setting, so that a typo does not silently bucket days wrongly.
4. As the dashboard user, I want the Grafana day panels to use the same zone as the apps, so that a half-hour never lands on different days in a summary and a panel.
5. As an operator, I want the README to say that the two apps' `timezone` values must match and what to do on a populated database when changing zone, so that I do not end up with mixed day boundaries unknowingly.
6. As a maintainer, I want one local-day module and no other `Europe/London` literals except where an external source documents its own zone, so that the rule has one home.

## Implementation Decisions

- **Shared module.** `libs/common/common/local_day.py` replaces octopus-app's `octopus_app/data/local_day.py`, keeping `to_local_date`, `start_of_local_day`, `expected_half_hour_count` and `HALF_HOUR` with the same signatures. All call sites in octopus-app import it from `common`; the old module is deleted.
- **Set once at startup, module-level.** The module holds the active `ZoneInfo`, defaulting to `Europe/London`. `set_local_timezone(name)` validates the name (an unknown zone raises a `ValueError` naming it) and replaces the active zone; a getter returns the active `ZoneInfo`. Each app's `main()` calls it once with `settings.timezone` before any job runs. Process-wide state is acceptable because each app process uses exactly one zone.
- **Setting.** A top-level optional `timezone: str = "Europe/London"` on both `ApplicationSettings` (octopus-app) and `HiveApplicationSettings` (hive-app). Validation happens at settings load, so a bad zone exits with a critical log naming `timezone`, like other invalid fields. Both config templates document it. The two apps read separate config files, so the values must match; the README says so.
- **hive-app.** `weather_backfill.py` drops `LOCAL_TIMEZONE` and uses the shared module for the local-day boundaries, the hour-stamp bucketing and `_local_yesterday`. `OpenMeteoClient.get_forecast` sends the active zone's key as the `timezone` request parameter. The archive request keeps `timezone=UTC` (hour-ending stamps are UTC; ADR-0029).
- **x2r.uk is the exception.** `x2r.py` reads naive timestamps as the zone its API documents, `Europe/London`, whatever the local timezone is. It gets a source-named constant (`X2R_TIMEZONE`) in place of the `LONDON` import. This is the one non-default `Europe/London` literal in app code.
- **Grafana.** `data/grafana/dashboard.json` gains a constant `timezone` template variable (default `Europe/London`, like the existing `region` variable), and every `'Europe/London'` literal inside the queries becomes `'${timezone}'`. `data/grafana/mariadb/queries.md` documents the variable and uses the same placeholder in its reference queries. `CONVERT_TZ` still needs MariaDB's named-timezone tables, as today.
- **Changing zone on a populated database.** Out of scope to automate. `daily_consumption_summary` rows keep the zone they were written in; the README gives the steps (stop octopus-app, set its timezone, delete the rows that have a `half_hour_count`, start it so the startup backfill rebuilds them); rows without a count (before Octopus's first available day) cannot be rebuilt and stay in the old zone. Weather rows are UTC hour stamps and need no change.
- **Docs.** ADR-0010 gets a dated update (local day is a setting; Python `zoneinfo` in the apps, `${timezone}` in Grafana). The glossary's **Local Day** and the "London day" wording that describes the rule (not this deployment) say "local day". The README documents the setting.

## Testing Decisions

- Test external behaviour at the existing seams, with the default zone covered by the unchanged existing tests (they all run under `Europe/London`) and the new zone covered by new tests. A fixture resets the active zone after each test so state never leaks.
- **libs/common:** the moved `test_local_day.py` (BST/GMT days, expected half-hour counts 46/48/50), plus the same assertions under `America/New_York` (its own 23- and 25-hour days), plus `set_local_timezone` rejecting an unknown name.
- **Settings:** each app's `get_settings` with no `timezone` gives `Europe/London`; with `America/New_York` keeps it; with an invalid name exits non-zero and the log names `timezone`.
- **octopus-app:** the existing consumption summarization seam (`test_consumption_summarization.py`) under `America/New_York`: a half-hour that is the next day in London but the same day in New York lands on the New York day.
- **hive-app:** the weather completeness report (`test_weather_history_backfill.py` seam) under `America/New_York` counts a New York DST day as 23/25 stamps; the forecast request seam (`test_weather_forecast_seam.py`) asserts the request `timezone` parameter follows the setting.
- **Startup wiring (added after Copilot review):** each app's real `main()` is driven with a config file holding `timezone: America/New_York`, with only the first client constructor replaced by a stand-in that records the active zone and stops `main()` there; the zone must already be New York. `test_octopus_main_startup_timezone.py`, `test_hive_main_startup_timezone.py`.
- **x2r:** a test that a naive x2r timestamp is read as London while the local timezone is `America/New_York`.
- **Grafana:** a test that scans `dashboard.json` and `queries.md` for `Europe/London` and finds none outside the variable's default, and that the `timezone` variable exists.
- Prior art: `apps/octopus-app/tests/test_local_day.py`, `test_consumption_summarization.py`, `apps/hive-app/tests/test_weather_history_backfill.py`, `test_weather_forecast_seam.py`, `test_x2r_client.py`.

## Out of Scope

- Rebuilding history automatically after a zone change on a populated database (documented only).
- Making the Octopus API, the region lookup or the Agile forecast sources non-GB.
- A single shared config file for both apps; each keeps its own `timezone` key.
- Running the Grafana panels against a live Grafana here; the dashboard edit is checked by the JSON scan and review only.

## Further Notes

- Issue #663; it blocks the heating model (#625), which relies on local days in octopus-app.
- Acceptance criterion changed from the issue's "Europe/London appears in code only as the default": it also appears as x2r.uk's documented zone, because that is a fact about the external source, not the household's zone.

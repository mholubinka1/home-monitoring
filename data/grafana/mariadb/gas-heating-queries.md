# Gas, Heating and Weather Dashboard Queries

One SQL block per panel of the second dashboard, [`data/grafana/gas-heating-dashboard.json`](../gas-heating-dashboard.json) ("pi-desktop: gas-heating-monitoring", uid `gas-heating-monitoring`). It is separate from the electricity dashboard ([`dashboard.json`](../dashboard.json), documented in [`queries.md`](queries.md)), which this file does not change. Re-sync this file and the JSON together whenever the dashboard changes.

**House style.** Every panel is a chart, with no tables; the one planned non-chart panel is the billing-period predictor (a bar gauge). Gas, thermostat and weather panels all read the same shared database as the electricity dashboard.

## Import

1. In Grafana, Dashboards, New, Import, upload `data/grafana/gas-heating-dashboard.json`, and map the `mysql` input to the MySQL data source.
2. That data source's **Database** field must be `home_monitoring`. Grafana queries the data source's Database field, not the `dataset` value stored in each panel (every target carries `"dataset": "home_monitoring"` as well, and a test checks it). A wrong Database field makes every panel fail with a "table doesn't exist" error; look at the Grafana container's log first.
3. The dashboard is imported, not provisioned, so edits made in the Grafana UI are lost on a re-import unless the JSON is re-exported and committed.

## Dashboard settings and variables

- **Time zone**: `Europe/London`, so every axis is in London time. All stored times are UTC and Grafana converts them for display.
- **Time range**: `now-21d` to `now`, refreshing every 5 minutes (the thermostat is polled every 2 minutes). Charts that look ahead (planned, daily gas with the forecast) carry their own range.
- `${timezone}`: hidden constant, default `Europe/London`. The zone that defines a local day for the daily panels added later; it must match the apps' `timezone` setting (see the README). Edit it in the `query` and `current` fields of the JSON, or a re-import puts the old zone back.

## Schema assumed

```text
heating_status       (hive-app) id, polled_at, current_temp, target_temp, mode, state,
                                boost_active, boost_ends_at, schedule, working
weather_observation  (hive-app) id, source, location, observed_at, temp, humidity, pressure,
                                wind_speed, precipitation, shortwave_radiation, cloud_cover,
                                sunshine_duration; unique (source, location, observed_at)
```

`heating_status.polled_at` and `weather_observation.observed_at` are UTC. `weather_observation.source` is `open-meteo` for live readings and `open-meteo-archive` for backfilled history; an hour can hold both, and the live one is preferred ([ADR-0029](../../../.agent-docs/adr/0029-weather-history-backfilled-hourly-deduplicated-and-labelled-by-source.md)). A `location` is the coordinates rounded to two decimals; the current location is the one on the newest stored observation, so a changed postcode never mixes two places.

---

## Row 1 — Temperatures

### Indoor, Target and Outdoor Temperature (time series)

Three lines: **indoor** (orange) and **target** (blue, drawn as a step because a target holds until changed) from the thermostat, and **outdoor** (green) from the weather. The gap between indoor and target is the demand for heat. There is no heating-on strip or shading. The minimum interval is 2 minutes, the poll interval, so the thermostat series shows every reading until the range is long enough that Grafana groups them.

Query A, thermostat (averaged into Grafana's interval bucket; a bucket with no reading is a gap, and gaps up to 30 minutes are bridged):

```sql
SELECT
  $__timeGroupAlias(polled_at, $__interval),
  AVG(current_temp) AS indoor,
  AVG(target_temp) AS target
FROM heating_status
WHERE $__timeFilter(polled_at)
GROUP BY 1
ORDER BY 1;
```

Query B, outdoor (hourly; the current location only; the live reading wins over the archive for the same hour):

```sql
SELECT
  w.observed_at AS time,
  w.temp AS outdoor
FROM weather_observation w
WHERE $__timeFilter(w.observed_at)
  AND w.temp IS NOT NULL
  AND w.location = (
    SELECT l.location FROM weather_observation l
    ORDER BY l.observed_at DESC LIMIT 1
  )
  AND (
    w.source <> 'open-meteo-archive'
    OR NOT EXISTS (
      SELECT 1 FROM weather_observation live
      WHERE live.location = w.location
        AND live.observed_at = w.observed_at
        AND live.source <> 'open-meteo-archive'
    )
  )
ORDER BY w.observed_at;
```

**Checked read-only against the Pi on 16 October 2026, for the 6 October test evening** (18:50 to 19:40 UTC, 24 readings): indoor held 20.6 to 20.8 C; the target read 7 C (frost) and went to 23 C for the boost at 19:14 to 19:18 and again at 19:24 (the boost flag was set at 19:18 and 19:24), matching the recorded heating-on windows. For 8 October 15:00 to 20:00 UTC the outdoor query returned one row per hour, taking the live reading where both sources existed (16:00: live 14.4 C over the archive's 13.8 C) and the archive's 13.8 C at 15:00 where only the archive had a reading.

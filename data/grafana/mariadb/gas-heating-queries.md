# Gas, Heating and Weather Dashboard Queries

One SQL block per panel of the second dashboard, [`data/grafana/gas-heating-dashboard.json`](../gas-heating-dashboard.json) ("pi-desktop: gas-heating-monitoring", uid `gas-heating-monitoring`). It is separate from the electricity dashboard ([`electricity-dashboard.json`](../electricity-dashboard.json), documented in [`electricity-queries.md`](electricity-queries.md)), which this file does not change. Re-sync this file and the JSON together whenever the dashboard changes.

**House style.** Every panel is a chart, with two exceptions: the billing-period predictor (a bar gauge) and the schedule table (a table, [feature-schedule-table-panel](../../../.agent-docs/specs/feature-schedule-table-panel.md)). Gas, thermostat and weather panels all read the same shared database as the electricity dashboard.

## Import

1. In Grafana, Dashboards, New, Import, upload `data/grafana/gas-heating-dashboard.json`, and map the `mysql` input to the MySQL data source.
2. That data source's **Database** field must be `home_monitoring`. Grafana queries the data source's Database field, not the `dataset` value stored in each panel (every target carries `"dataset": "home_monitoring"` as well, and a test checks it). A wrong Database field makes every panel fail with a "table doesn't exist" error; look at the Grafana container's log first.
3. The dashboard is imported, not provisioned, so edits made in the Grafana UI are lost on a re-import unless the JSON is re-exported and committed.

## Dashboard settings and variables

- **Time zone**: `Europe/London`, so every axis is in London time. All stored times are UTC and Grafana converts them for display.
- **Time range**: `now-14d` to `now+7d`, refreshing every 5 minutes (the thermostat is polled every 2 minutes). A Grafana time chart can only draw as far right as the dashboard range ends, so the end is 7 days ahead for the forecast outdoor temperature on the temperatures panel.
- `${timezone}`: hidden constant, default `Europe/London`. The zone that defines a local day for the daily panels (every `CONVERT_TZ` below); it must match the apps' `timezone` setting (see the README). Edit it in the `query` and `current` fields of the JSON, or a re-import puts the old zone back.

- `${region}`: the GSP region code, `SELECT DISTINCT region FROM product_rate;`, for the gas rate lookups.
- `${gas_unit}`: `kWh` or `pounds` (default `kWh`), the unit of the Daily Gas panel.
- `${wake_up}`: text box, default `7`. The hour the waking window starts, in whole hours 0-23 (London time), for the indoor average on the Daily Outdoor Temperature Band.
- `${bedtime}`: text box, default `23`. The hour the waking window ends (exclusive), in whole hours 0-23, after `${wake_up}`; otherwise the indoor line shows no data.
- `${gas_billing_period_start}`, `${gas_billing_period_end}`, `${gas_rates_estimated_note}`: hidden, from the latest gas `cost_forecast` row; used only in the predictor's title.

## Schema assumed

```text
heating_status       (hive-app) id, polled_at, current_temp, target_temp, mode, state,
                                boost_active, boost_ends_at, schedule, working
weather_observation  (hive-app) id, source, location, observed_at, temp, humidity, pressure,
                                wind_speed, precipitation, shortwave_radiation, cloud_cover,
                                sunshine_duration; unique (source, location, observed_at)
weather_forecast     (hive-app) id, source, target_date, max_temp, mean_temp, fetched_at
                                (one row per forecast day; mean_temp can be NULL)
consumption               (octopus) id, energy, period_from, period_to, raw_value, unit, est_kwh
daily_consumption_summary (octopus) energy, date, total_kwh, half_hour_count
agreement                 (octopus) id, energy, product_code, tariff_code, valid_from, valid_to
product_rate              (octopus) id, product_code, region, valid_from, valid_to, unit_rate, standing_charge
cost_forecast             (octopus) billing_period_start/end, actual_cost_to_date,
                                    projected_total_cost, computed_at, energy, rates_estimated, estimated_days
heating_model             (octopus) id, fitted_at, usable, threshold, slope, weight,
                                    r_squared, baseload, ...
heating_forecast_day      (octopus) date, model_id, forecast_mean, effective_temp, expected_gas, computed_at
heating_interval          (octopus) starts_at (UTC, key), ends_at, poll_count, on_minutes, measured_kwh,
                                    heating_kwh, source ('measured' or 'estimated'), unit_rate (p/kWh), cost_gbp,
                                    computed_at
comfort_day               (octopus) date (London day, key), comfort_temp (C, NULL until learned), boost_starts,
                                    counted_hours ('0,1,6,18'), minutes_below (NULL without a comfort_temp), computed_at
```

`heating_status.polled_at` and `weather_observation.observed_at` are UTC. `weather_observation.source` is `open-meteo` for live readings and `open-meteo-archive` for backfilled history; an hour can hold both, and the live one is preferred ([ADR-0029](../../../.agent-docs/adr/0029-weather-history-backfilled-hourly-deduplicated-and-labelled-by-source.md)). A `location` is the coordinates rounded to two decimals; the current location is the one on the newest stored observation (ties on the same hour go to the newest row id), so a changed postcode never mixes two places.

---

## Row 1 — Temperatures

### Indoor, Target and Outdoor Temperature (time series)

Four lines and a forecast: **comfort** (purple, a step: the learned comfort temperature, see query D), **indoor** (orange) and **target** (blue, drawn as a step because a target holds until changed) from the thermostat, and **outdoor** (green) from the weather, then a dashed **outdoor forecast** (green) that continues the outdoor line to the right with the forecast daily mean temperature. The gap between indoor and target is the demand for heat. There is no heating-on strip or shading. The minimum interval is 2 minutes, the poll interval, so the thermostat series shows every reading until the range is long enough that Grafana groups them (Grafana picks the interval from the panel width and the range).

Query A, thermostat (indoor is the average of the readings in each of Grafana's interval buckets; the target is the last non-NULL reading in the bucket, so a step is not blurred into an in-between value; each row is stamped at the start of its bucket, so on a long range a step can appear up to one interval early, and at the 2-minute minimum it is exact). On a long range a change that lasts less than one interval can disappear: at the default 21 days Grafana groups the readings into buckets of about 16 minutes, and the 6 October boost (23 C from 19:14 to 19:18, back to 7 C at 19:20) falls inside one bucket whose last reading is 7 C. Zoom in to see short steps; the Heating Cost per Hour panel counts every hour the heating was on, at any range. A bucket with no reading has no row, and every line is joined across missing points (the two series have different spacing, so a gap threshold would hide one of them on a long range); a thermostat outage therefore shows as a straight line, so read it with the hive-app health in mind. `GROUP_CONCAT` is not truncated on MariaDB (its limit is 1 MB); on a MySQL server with the 1,024-byte default a very wide bucket would return a stale target:

```sql
SELECT
  $__timeGroupAlias(polled_at, $__interval),
  AVG(current_temp) AS indoor,
  CAST(
    SUBSTRING_INDEX(GROUP_CONCAT(target_temp ORDER BY polled_at), ',', -1)
    AS DECIMAL(5, 2)
  ) AS target
FROM heating_status
WHERE $__timeFilter(polled_at)
GROUP BY 1
ORDER BY 1;
```

Query B, outdoor (hourly; the current location only; the readings of an hour are ranked, a live one before an archive one, and the first is kept; a reading with no temperature is dropped first, so it never hides the other source's reading):

```sql
SELECT
  observed_at AS time,
  temp AS outdoor
FROM (
  SELECT
    w.observed_at,
    w.temp,
    ROW_NUMBER() OVER (
      PARTITION BY w.observed_at
      ORDER BY (COALESCE(w.source, '') = 'open-meteo-archive'), w.id
    ) AS preference
  FROM weather_observation w
  WHERE $__timeFilter(w.observed_at)
    AND w.temp IS NOT NULL
    AND w.location = (
      SELECT l.location FROM weather_observation l
      ORDER BY l.observed_at DESC, l.id DESC LIMIT 1
    )
) ranked
WHERE preference = 1
ORDER BY observed_at;
```

**Checked read-only against the Pi on 16 October 2026, for the 6 October test evening** (24 readings between 18:50 and 19:40 UTC; the rows around the boosts are listed below): indoor held 20.6 to 20.8 C; the target read 7 C (frost) and stepped to 23 C in the 19:14 to 19:18 buckets and again at 19:24, with the boost flag set in the 19:18 and 19:24 buckets, consistent with the recorded heating-on windows to bucket resolution (the boost flag comes from a separate read of the same rows; the target rises with the demand, and the exact on and off times are within a bucket). Rows at 2-minute buckets (indoor C, target C, boost flag): 19:12 20.6, 7, 0; 19:14 20.7, 23, 0; 19:16 20.7, 23, 0; 19:18 20.7, 23, 1; 19:20 20.8, 7, 0; 19:22 20.7, 7, 0; 19:24 20.7, 23, 1; 19:26 20.8, 7, 0 (the 19:00 bucket has no reading). The target query keeps the last reading of each bucket (the 6 October evening's 7 to 23 C steps come through intact at 2-minute buckets). Over 25 September to 16 October (the thermostat history starts on 3 October, so about 13 days of readings), with 16-minute buckets (an example; Grafana chooses the interval), it returns about 1,160 buckets in under a second, and the outdoor query 519 hourly rows. The daylight-saving check for this panel is deferred until after the first London clock change on 25 October 2026 (the thermostat history starts on 3 October); the panel groups by UTC time and Grafana converts for display, so nothing in its queries depends on the local day. For 8 October 15:00 to 20:00 UTC the outdoor query returned one row per hour, taking the live reading where both sources existed (16:00: live 14.4 C over the archive's 13.8 C) and the archive's 13.8 C at 15:00 where only the archive had a reading.

Query C, outdoor forecast (the forecast is one mean per day, so it is stamped at 12:00 London on its day, and a day only draws when that stamp is later than the newest outdoor reading of the current location and the day is today or later (so a stalled weather feed does not draw forecast over days that have passed); the newest reading itself is included once, so the dashed line starts where the solid one ends; when several forecast rows exist for a day (one per source; the `id` is a text value, source name and date, so it says nothing about age) the one with the latest `fetched_at` is used). As real readings arrive they pass the stamp and the forecast day drops out, so actuals overwrite the forecast:

```sql
WITH latest AS (
  SELECT w.observed_at, w.temp
  FROM weather_observation w
  WHERE w.temp IS NOT NULL
    AND w.location = (
      SELECT l.location FROM weather_observation l
      ORDER BY l.observed_at DESC, l.id DESC LIMIT 1
    )
  ORDER BY w.observed_at DESC, (COALESCE(w.source, '') = 'open-meteo-archive'), w.id
  LIMIT 1
)
SELECT time, outdoor_forecast
FROM (
  SELECT observed_at AS time, temp AS outdoor_forecast
  FROM latest

  UNION ALL

  SELECT
    CONVERT_TZ(CAST(f.target_date AS DATETIME) + INTERVAL 12 HOUR, '${timezone}', 'UTC') AS time,
    f.mean_temp AS outdoor_forecast
  FROM weather_forecast f
  CROSS JOIN latest
  WHERE f.mean_temp IS NOT NULL
    AND f.target_date >= DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}'))
    AND f.id = (
      SELECT f2.id FROM weather_forecast f2
      WHERE f2.target_date = f.target_date AND f2.mean_temp IS NOT NULL
      ORDER BY f2.fetched_at DESC, f2.id DESC LIMIT 1
    )
    AND CONVERT_TZ(CAST(f.target_date AS DATETIME) + INTERVAL 12 HOUR, '${timezone}', 'UTC') > latest.observed_at
) d
WHERE $__timeFilter(time)
ORDER BY time;
```

**Checked read-only against the Pi on 20 October 2026 at 20:07 UTC.** The newest outdoor reading was 20:00 UTC at 11.5 C, and the query returned that point followed by the forecast days 21 to 26 October: 21 October 9.8 C stamped 11:00 UTC (midday BST), 25 October 11.5 C stamped 12:00 UTC (midday GMT, the clocks having changed on the night of 24 October), and so on. 20 October's own forecast (11.9 C, stamped 11:00 UTC) is absent because readings already cover it.

Query D, comfort (the learned **Comfort Temperature** of each London day from the small `comfort_day` table that octopus-app's hourly job writes ([ADR-0034](../../../.agent-docs/adr/0034-comfort-temperature-is-learned-from-the-room-temperature-at-boost-starts.md)): the weighted median room temperature at the boost starts of the two years before the day, a boost counting half as much for every 60 heating days since it. One point per day at London midnight, drawn as a step, plus one closing point so the line reaches the right-hand edge: the value of the newest day that starts within the range, repeated at the end of that day or at the end of the range, whichever is sooner (so a range ending now, or part-way through a past day, still draws that day). A day with no comfort temperature yet has no point. The range starts a day early so the step covers the left edge of the chart):

```sql
SELECT time, comfort
FROM (
  SELECT
    CONVERT_TZ(CAST(c.date AS DATETIME), '${timezone}', 'UTC') AS time,
    c.comfort_temp AS comfort
  FROM comfort_day c
  WHERE c.comfort_temp IS NOT NULL

  UNION ALL

  SELECT
    LEAST(
      CONVERT_TZ(CAST(c.date AS DATETIME) + INTERVAL 1 DAY, '${timezone}', 'UTC'),
      $__timeTo()
    ) AS time,
    c.comfort_temp AS comfort
  FROM comfort_day c
  WHERE c.date = (
    SELECT MAX(l.date) FROM comfort_day l
    WHERE l.comfort_temp IS NOT NULL
      AND CONVERT_TZ(CAST(l.date AS DATETIME), '${timezone}', 'UTC') <= $__timeTo()
  )
) d
WHERE time >= $__timeFrom() - INTERVAL 1 DAY
  AND time <= $__timeTo()
ORDER BY time;
```

**Not yet checked against the Pi.** Checked once, in the final dashboard review, on a throwaway copy of the database. Deploy octopus-app first so the table exists, then import the dashboard.

---

Query E, room forecast (the predicted room temperature under the Hive schedule as programmed and under the comfort plan, from octopus-app's hourly room forecast job: one point every 10 minutes from the step holding the newest reading to the end of tomorrow, drawn as dotted lines; `room_forecast_interval` rows carry their own `starts_at` and `ends_at`, so the query assumes no length):

```sql
SELECT
  r.starts_at AS time,
  MAX(CASE WHEN r.scenario = 'current' THEN r.room_temp END) AS room_current,
  MAX(CASE WHEN r.scenario = 'plan' THEN r.room_temp END) AS room_plan
FROM room_forecast_interval r
WHERE r.kind = 'live'
  AND r.scenario IN ('current', 'plan')
  AND $__timeFilter(r.starts_at)
GROUP BY r.starts_at
ORDER BY r.starts_at;
```

Not yet run against MariaDB: it is checked in the single end-of-plan dashboard review.

## Row 2 — Gas

**Complete Gas Day.** Every gas panel that reads `daily_consumption_summary` keeps a day only when its total is above zero and its stored half-hour count is the number the local day should have (46, 48 or 50 on clock-change days); a day with no stored count (before the API's first day) is kept when its total is above zero. A stored 0.0 therefore never draws as zero use, and a partial day never draws as a low day. See **Complete Gas Day** in `.agent-docs/context.md`. The same condition is repeated in each query:

```sql
s.energy = 'G'
AND s.total_kwh > 0
AND (
  s.half_hour_count IS NULL
  OR s.half_hour_count = TIMESTAMPDIFF(
    MINUTE,
    CONVERT_TZ(CAST(s.date AS DATETIME), '${timezone}', 'UTC'),
    CONVERT_TZ(CAST(s.date + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
  ) / 30
)
```

**Daily timestamps.** A daily figure is stamped at its London midnight, converted to UTC (`CONVERT_TZ(CAST(day AS DATETIME), '${timezone}', 'UTC')`), so a bar sits on the right local day and a clock-change day is its real 23 or 25 hours. The time range is applied to that timestamp from an outer query (`$__timeFilter(time)`).

**Gas rates for a day.** The gas agreement and the rate row in force at the day's London midnight (the same correlated lookup as the electricity panels: the latest `product_rate` row with `valid_from` at or before the day start).

**Repeated fragments.** The hourly outdoor CTE (`wx` and `daily`) is copied into the daily gas panel's query C and the band query and the Complete Gas Day condition into the two daily gas queries, on purpose: each query stands alone in Grafana, so a later change has to be made in every copy (the tests check the copies exist).

### Daily Gas and Outdoor Temperature (time series)

Bars of daily gas, in kWh or pounds (the **Daily gas unit** variable, `kWh` or `pounds`), with the daily mean outdoor temperature as a line on the right axis. Only A and D (kWh) or B and E (pounds) return rows, whichever unit is selected (the text, unit and region variables enter the SQL as `${name:sqlstring}`, which quotes and escapes them). In pounds the bars stack: the unit cost (kWh x the unit rate) and the standing charge, each in pounds (rounded to 4 decimals in SQL; the panel shows two). A day with no complete gas figure has no bar in either unit, standing charge included. A day is priced at the rate in force at its London midnight; the cost forecast prices each reading at its own time's rate, so the two agree except on a day when the rate changes. The temperature lines show a missing day as a break (a null is inserted after 36 hours), not a line across it.

Query A, kWh:

```sql
SELECT time, gas_kwh
FROM (
  SELECT
    CONVERT_TZ(CAST(s.date AS DATETIME), '${timezone}', 'UTC') AS time,
    s.total_kwh AS gas_kwh
  FROM daily_consumption_summary s
  WHERE ${gas_unit:sqlstring} = 'kWh'
    AND s.energy = 'G'
    AND s.total_kwh > 0
    AND (
      s.half_hour_count IS NULL
      OR s.half_hour_count = TIMESTAMPDIFF(
        MINUTE,
        CONVERT_TZ(CAST(s.date AS DATETIME), '${timezone}', 'UTC'),
        CONVERT_TZ(CAST(s.date + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
      ) / 30
    )
) d
WHERE $__timeFilter(time)
ORDER BY time;
```

Query B, pounds:

```sql
SELECT time, unit_cost_gbp, standing_charge_gbp
FROM (
  SELECT
    CONVERT_TZ(CAST(s.date AS DATETIME), '${timezone}', 'UTC') AS time,
    ROUND(s.total_kwh * pr.unit_rate / 100, 4) AS unit_cost_gbp,
    ROUND(pr.standing_charge / 100, 4) AS standing_charge_gbp
  FROM daily_consumption_summary s
  JOIN agreement a
    ON a.energy = 'G'
   AND CONVERT_TZ(CAST(s.date AS DATETIME), '${timezone}', 'UTC') >= a.valid_from
   AND CONVERT_TZ(CAST(s.date AS DATETIME), '${timezone}', 'UTC') < COALESCE(a.valid_to, '9999-12-31 23:59:59')
  JOIN product_rate pr
    ON pr.id = (
      SELECT pr2.id FROM product_rate pr2
      WHERE pr2.product_code = a.product_code
        AND pr2.region = ${region:sqlstring}
        AND pr2.valid_from <= CONVERT_TZ(CAST(s.date AS DATETIME), '${timezone}', 'UTC')
      ORDER BY pr2.valid_from DESC
      LIMIT 1
    )
  WHERE ${gas_unit:sqlstring} = 'pounds'
    AND s.energy = 'G'
    AND s.total_kwh > 0
    AND (
      s.half_hour_count IS NULL
      OR s.half_hour_count = TIMESTAMPDIFF(
        MINUTE,
        CONVERT_TZ(CAST(s.date AS DATETIME), '${timezone}', 'UTC'),
        CONVERT_TZ(CAST(s.date + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
      ) / 30
    )
) d
WHERE $__timeFilter(time)
ORDER BY time;
```

Query C, daily mean outdoor temperature (complete London days only: every hour of the local day has a reading, 23, 24 or 25 hours; one reading per hour at the current location, ranked with a window function so the live one is kept over the archive):

```sql
WITH wx AS (
  SELECT observed_at, temp
  FROM (
    SELECT
      w.observed_at, w.temp,
      ROW_NUMBER() OVER (
        PARTITION BY w.observed_at
        ORDER BY (COALESCE(w.source, '') = 'open-meteo-archive'), w.id
      ) AS preference
    FROM weather_observation w
    WHERE w.temp IS NOT NULL
      AND w.observed_at >= $__timeFrom() - INTERVAL 2 DAY
      AND w.observed_at <= $__timeTo() + INTERVAL 1 DAY
      AND w.location = (
        SELECT l.location FROM weather_observation l
        ORDER BY l.observed_at DESC, l.id DESC LIMIT 1
      )
  ) ranked
  WHERE preference = 1
),
daily AS (
  SELECT
    DATE(CONVERT_TZ(observed_at, 'UTC', '${timezone}')) AS day,
    AVG(temp) AS mean_temp,
    MIN(temp) AS min_temp,
    MAX(temp) AS max_temp
  FROM wx
  GROUP BY DATE(CONVERT_TZ(observed_at, 'UTC', '${timezone}'))
  HAVING COUNT(*) = TIMESTAMPDIFF(
    HOUR,
    CONVERT_TZ(CAST(day AS DATETIME), '${timezone}', 'UTC'),
    CONVERT_TZ(CAST(day + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
  )
)
SELECT time, outdoor_mean
FROM (
  SELECT
    CONVERT_TZ(CAST(day AS DATETIME), '${timezone}', 'UTC') AS time,
    ROUND(mean_temp, 1) AS outdoor_mean
  FROM daily
) d
WHERE $__timeFilter(time)
ORDER BY time;
```

**Checked read-only against the Pi on 16 October 2026.**

- **kWh (A):** 3 to 14 October came back as the stored totals (13.83504, 13.41988, 6.92313, ..., 13.45352), each stamped at its London midnight (`2026-10-02 23:00:00` UTC is 3 October, BST). 15 October (stored total 0.0, 2 readings) is absent: a gap, not a zero.
- **Clock-change days:** 30 March 2025 (46 half-hours, 9.15604 kWh, outdoor mean 11.7 C from 23 hourly readings) and 26 October 2025 (50 half-hours, 28.11891 kWh, outdoor mean 7.5 C from 25 hourly readings) both came back as complete days, stamped at their London midnights (30 March 2025 00:00 UTC, the day after at 30 March 23:00 UTC because BST has begun; 26 October 2025 at 25 October 23:00 UTC, the day after at 27 October 00:00 UTC). 29 March 2026 (46 half-hours) is also stored. The 25 October 2026 change has not happened yet.
- **Pounds (B):** 14 October is unit cost 1.1548 (13.45352 kWh x 8.58396p) plus standing charge 0.2924, 1.4472 together; the same day rebuilt from the raw half-hour readings with the cost forecast's own formula (sum of kWh x unit rate, plus the standing charge) is 1.4473. Pounds only exist from 1 July 2026: the Pi holds gas rates from 30 June 2026 only, so earlier days have no rate and draw as gaps in the pounds view.
- **Outdoor (C):** 3 October 15.7 C, 4 October 16.2 C, ... 15 October 17.2 C, and none for the current day (not complete yet).

Queries D and E, the expected gas bars (lighter bars for today and the next days, from `heating_forecast_day`; D when the unit is kWh, E when it is pounds, like A and B). Only the forecast rows computed from the **latest usable** heating model are used (the cost forecast makes the same choice, so a refit that fails never leaves the bars on a stale model), and only from today's London date on, so a stalled model job cannot draw expected bars over days that have passed. With no usable model there are no expected bars. In pounds the expected kWh is priced with the rate and standing charge in force at the day's London midnight, the same lookup as query B, so a rate change inside the week is not foreseen, and a forecast day with no agreement or rate row has no pounds bar (a gap, not a zero). The bars are labelled `estimated` because they are a projection. The forecast outdoor temperature that used to sit beside them on this panel now lives on the temperatures panel. The bars stack the same way as the actual ones and are drawn with a lower fill opacity and an `expected` label.

```sql
SELECT time, expected_kwh
FROM (
  SELECT
    CONVERT_TZ(CAST(f.date AS DATETIME), '${timezone}', 'UTC') AS time,
    ROUND(f.expected_gas, 3) AS expected_kwh
  FROM heating_forecast_day f
  WHERE ${gas_unit:sqlstring} = 'kWh'
    AND f.model_id = (SELECT m.id FROM heating_model m WHERE m.usable = 1 ORDER BY m.fitted_at DESC, m.id DESC LIMIT 1)
    AND f.date >= DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}'))
) d
WHERE $__timeFilter(time)
ORDER BY time;
```

```sql
SELECT time, expected_unit_cost_gbp, expected_standing_charge_gbp
FROM (
  SELECT
    CONVERT_TZ(CAST(f.date AS DATETIME), '${timezone}', 'UTC') AS time,
    ROUND(f.expected_gas * pr.unit_rate / 100, 4) AS expected_unit_cost_gbp,
    ROUND(pr.standing_charge / 100, 4) AS expected_standing_charge_gbp
  FROM heating_forecast_day f
  JOIN agreement a
    ON a.energy = 'G'
   AND CONVERT_TZ(CAST(f.date AS DATETIME), '${timezone}', 'UTC') >= a.valid_from
   AND CONVERT_TZ(CAST(f.date AS DATETIME), '${timezone}', 'UTC') < COALESCE(a.valid_to, '9999-12-31 23:59:59')
  JOIN product_rate pr
    ON pr.id = (
      SELECT pr2.id FROM product_rate pr2
      WHERE pr2.product_code = a.product_code
        AND pr2.region = ${region:sqlstring}
        AND pr2.valid_from <= CONVERT_TZ(CAST(f.date AS DATETIME), '${timezone}', 'UTC')
      ORDER BY pr2.valid_from DESC
      LIMIT 1
    )
  WHERE ${gas_unit:sqlstring} = 'pounds'
    AND f.model_id = (SELECT m.id FROM heating_model m WHERE m.usable = 1 ORDER BY m.fitted_at DESC, m.id DESC LIMIT 1)
    AND f.date >= DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}'))
) d
WHERE $__timeFilter(time)
ORDER BY time;
```

**Checked read-only against the Pi on 27 October 2026.** Query D returned 27 October to 2 November (8.231, 8.231, 12.734, 14.160, 14.235, 16.112, 18.063 kWh); query E returned the same days in pounds (27 October 0.7065 unit cost, 8.231 kWh at 8.58p, plus the 0.2924 standing charge). D returns nothing while the unit is pounds and E nothing while it is kWh. With the usable-model test made to match no model (`m.usable = 2`), D and E return no rows, so there are no expected bars.

### Heating Cost per Half-Hour (time series)

Stacked bars of the gas cost of the heating in each completed half-hour, in pounds, read from the small `heating_interval` table that octopus-app's hourly job writes (ADR-0033 with the hour replaced by the half-hour, the gas meter's grain; [ADR-0036](../../../.agent-docs/adr/0036-room-model-times-at-two-minutes-learns-at-ten-and-has-a-warm-up-delay.md)), so the panel does no heavy work on the Pi. Heating is any half-hour the thermostat called for heat or a boost was on (`state = 'ON' OR boost_active = 1`, counted by the job). Each bar is one of two series: **measured** (solid; Octopus's gas for the half-hour less a learned hot-water baseline) or **estimated** (lighter; the minutes on times the boiler's learned effective kW, used until the gas arrives about 1.5 days later). Cost is the gas unit rate in force at the start of the half-hour, with no standing charge. A half-hour with fewer than 12 of its 15 Hive readings has no row, so it is a gap, never a zero. Half-hours with the heating off (on-minutes 0) are not drawn. The panel looks back over the dashboard's 14 days and shows nothing for the forward 7.

```sql
SELECT time, measured_cost_gbp, estimated_cost_gbp
FROM (
  SELECT
    h.starts_at AS time,
    IF(h.source = 'measured', ROUND(h.cost_gbp, 4), NULL) AS measured_cost_gbp,
    IF(h.source = 'estimated', ROUND(h.cost_gbp, 4), NULL) AS estimated_cost_gbp
  FROM heating_interval h
  WHERE $__timeFilter(h.starts_at)
    AND h.on_minutes > 0
    AND h.cost_gbp IS NOT NULL
) d
ORDER BY time;
```

**Not yet checked against the Pi.** This query is checked once, in the final dashboard review, read-only on a throwaway copy of the database (never on the live MariaDB, which has a 512 MB limit). Deploy octopus-app first so the table exists and is filled, then import the dashboard.

### Minutes Below Comfort (time series)

Bars of minutes per London day in which the room was below the day's learned **Comfort Temperature** during a **Counted Hour** (a London clock hour in which the heating was on at least once in the 365 days before the day). octopus-app's hourly job works the figure out from the 2-minute thermostat readings and stores it in `comfort_day` ([ADR-0034](../../../.agent-docs/adr/0034-comfort-temperature-is-learned-from-the-room-temperature-at-boost-starts.md)), so the panel reads one small row per day. Today's bar grows through the day; a finished day is settled once and never changes. A day with no comfort temperature yet (fewer than 5 boosts seen) or with no thermostat readings is a gap, not a zero. There are no settings: the old **Comfort target** and **Margin below target** text boxes are gone. Minutes assume the 2-minute poll interval hive-app uses (`HEATING_REFRESH_INTERVAL_SECONDS = 120`); if that changes, octopus-app's `POLL_MINUTES` and `MIN_POLLS_PER_INTERVAL` must change too.

```sql
SELECT time, minutes_below_comfort
FROM (
  SELECT
    CONVERT_TZ(CAST(c.date AS DATETIME), '${timezone}', 'UTC') AS time,
    c.minutes_below AS minutes_below_comfort
  FROM comfort_day c
  WHERE c.minutes_below IS NOT NULL
) d
WHERE $__timeFilter(time)
ORDER BY time;
```

**Not yet checked against the Pi.** Checked once, in the final dashboard review, on a throwaway copy of the database. Deploy octopus-app first so the table exists and is filled, then import the dashboard.

### Daily Outdoor Temperature Band (time series)

Daily minimum and maximum outdoor temperature for complete London days, shaded between the minimum and maximum (the maximum fills down to the series displayed as "minimum"), plus the indoor average over waking hours ("indoor (waking hours)"): the mean of the thermostat's `current_temp` over polls whose London hour is at or after `${wake_up}` and before `${bedtime}`. A day gets an indoor value only when it has at least 80% of the polls expected in that window (one every 2 minutes, so `(bedtime - wake_up) * 30`). Both hours must be whole numbers 0-23 with bedtime after wake up; anything else gives no indoor line. The outdoor part uses the same hourly outdoor readings and complete-day rule as the daily gas panel's query C. Hive-app still records sunshine and solar radiation, but no panel draws them. The panel follows the dashboard time range (14 days by default), so widen the time picker to see the history, which starts where the weather backfill starts (24 July 2024; the first complete London day is 25 July). A missing day is a break in the lines. The dotted forecast minimum and maximum (queries B and C below) continue the lines from today onward.

```sql
WITH wx AS (
  SELECT observed_at, temp
  FROM (
    SELECT
      w.observed_at, w.temp,
      ROW_NUMBER() OVER (
        PARTITION BY w.observed_at
        ORDER BY (COALESCE(w.source, '') = 'open-meteo-archive'), w.id
      ) AS preference
    FROM weather_observation w
    WHERE w.temp IS NOT NULL
      AND w.observed_at >= $__timeFrom() - INTERVAL 2 DAY
      AND w.observed_at <= $__timeTo() + INTERVAL 1 DAY
      AND w.location = (
        SELECT l.location FROM weather_observation l
        ORDER BY l.observed_at DESC, l.id DESC LIMIT 1
      )
  ) ranked
  WHERE preference = 1
),
daily AS (
  SELECT
    DATE(CONVERT_TZ(observed_at, 'UTC', '${timezone}')) AS day,
    MIN(temp) AS min_temp,
    MAX(temp) AS max_temp
  FROM wx
  GROUP BY DATE(CONVERT_TZ(observed_at, 'UTC', '${timezone}'))
  HAVING COUNT(*) = TIMESTAMPDIFF(
    HOUR,
    CONVERT_TZ(CAST(day AS DATETIME), '${timezone}', 'UTC'),
    CONVERT_TZ(CAST(day + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
  )
),
waking AS (
  SELECT IF(b > w, w, NULL) AS wake_up, IF(b > w, b, NULL) AS bedtime
  FROM (
    SELECT
      IF(${wake_up:sqlstring} REGEXP '^[0-9]{1,2}$' AND CAST(${wake_up:sqlstring} AS SIGNED) <= 23, CAST(${wake_up:sqlstring} AS SIGNED), NULL) AS w,
      IF(${bedtime:sqlstring} REGEXP '^[0-9]{1,2}$' AND CAST(${bedtime:sqlstring} AS SIGNED) <= 23, CAST(${bedtime:sqlstring} AS SIGNED), NULL) AS b
  ) r
),
indoor AS (
  SELECT DATE(h.local_at) AS day, AVG(h.current_temp) AS indoor_avg
  FROM (
    SELECT CONVERT_TZ(polled_at, 'UTC', '${timezone}') AS local_at, current_temp
    FROM heating_status
    WHERE current_temp IS NOT NULL
      AND polled_at >= $__timeFrom() - INTERVAL 2 DAY
      AND polled_at <= $__timeTo() + INTERVAL 1 DAY
  ) h
  CROSS JOIN waking p
  WHERE HOUR(h.local_at) >= p.wake_up AND HOUR(h.local_at) < p.bedtime
  GROUP BY DATE(h.local_at), p.wake_up, p.bedtime
  HAVING COUNT(*) >= 0.8 * (p.bedtime - p.wake_up) * 30
),
days AS (
  SELECT day FROM daily
  UNION
  SELECT day FROM indoor
)
SELECT time, min_temp, max_temp, indoor_avg
FROM (
  SELECT
    CONVERT_TZ(CAST(days.day AS DATETIME), '${timezone}', 'UTC') AS time,
    ROUND(daily.min_temp, 1) AS min_temp,
    ROUND(daily.max_temp, 1) AS max_temp,
    ROUND(indoor.indoor_avg, 1) AS indoor_avg
  FROM days
  LEFT JOIN daily ON daily.day = days.day
  LEFT JOIN indoor ON indoor.day = days.day
) d
WHERE $__timeFilter(time)
ORDER BY time;
```

Two more queries on the same panel continue the minimum and maximum as dotted forecast lines (same colours as the observed lines, no shading), one point per forecast day from today (London) onward at London midnight like the observed days. hive-app stores one row per source and date, overwritten by each fetch, so each query reads that row; if a second source ever wrote the same date, the newest fetch wins (`ORDER BY fetched_at DESC, id DESC`; the id is text, so never `MAX(id)`). A day whose forecast has no minimum is absent from the minimum line only and still on the maximum line. Deploy hive-app before importing the dashboard so the `min_temp` column exists (the minimum line stays empty until the next hourly forecast fetch fills it). Past days never appear. Daily points are one day apart, below the panel's 1.5-day `insertNulls` threshold, so consecutive forecast days join.

Query B, `forecast_min` (displayed as "forecast minimum"):

```sql
SELECT time, forecast_min
FROM (
  SELECT
    CONVERT_TZ(CAST(f.target_date AS DATETIME), '${timezone}', 'UTC') AS time,
    ROUND(f.min_temp, 1) AS forecast_min
  FROM weather_forecast f
  WHERE f.min_temp IS NOT NULL
    AND f.target_date >= DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}'))
    AND f.id = (
      SELECT f2.id FROM weather_forecast f2
      WHERE f2.target_date = f.target_date AND f2.min_temp IS NOT NULL
      ORDER BY f2.fetched_at DESC, f2.id DESC LIMIT 1
    )
) d
WHERE $__timeFilter(time)
ORDER BY time;
```

Query C, `forecast_max` (displayed as "forecast maximum"):

```sql
SELECT time, forecast_max
FROM (
  SELECT
    CONVERT_TZ(CAST(f.target_date AS DATETIME), '${timezone}', 'UTC') AS time,
    ROUND(f.max_temp, 1) AS forecast_max
  FROM weather_forecast f
  WHERE f.max_temp IS NOT NULL
    AND f.target_date >= DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}'))
    AND f.id = (
      SELECT f2.id FROM weather_forecast f2
      WHERE f2.target_date = f.target_date AND f2.max_temp IS NOT NULL
      ORDER BY f2.fetched_at DESC, f2.id DESC LIMIT 1
    )
) d
WHERE $__timeFilter(time)
ORDER BY time;
```

**Checked read-only against the Pi on 16 October 2026.** 3 October: minimum 12.4, mean 15.7, maximum 19.3 C; 6 October: 10.1, 13.1, 14.6 C. History starts on 25 July 2024, the first complete London day after the backfill's first reading (24 July 2024 00:00 UTC is 01:00 BST): 15.4, 17.4, 19.8 C. Over the whole history (25 July 2024 to 15 October 2026, 813 complete days) the query runs in about a second; an earlier version that looked each hour up with a correlated `NOT EXISTS` could not use the table's index and took over two minutes, so the live-over-archive choice is now a window-function ranking.

### Gas Billing Period Progress (bar gauge)

The one non-chart panel, mirroring "Billing Period Progress" on the electricity dashboard: the latest gas row of the cost forecast, the cost so far against the projected total. It shows whatever the cost forecast currently computes (its colour thresholds, 60, 80 and 100 pounds, are copied from the electricity panel, so a gas projection near 33 pounds stays green; the bar length is what carries the information); since the heating model was adopted, each remaining day's expected gas comes from the model (the flat average beyond its week, and for every day when there is no usable fit). Its title uses the hidden `gas_billing_period_start`, `gas_billing_period_end` and `gas_rates_estimated_note` variables (the latest gas `cost_forecast` row, refreshing on a time range change like the electricity ones).

```sql
SELECT actual_cost_to_date AS billing_period_cost_gbp, projected_total_cost AS projected_cost_gbp
FROM cost_forecast
WHERE energy = 'G'
ORDER BY computed_at DESC
LIMIT 1;
```

**Checked read-only against the Pi on 16 October 2026:** 12.41 cost so far, 32.76 projected, the same figures as the latest gas row of `cost_forecast`.

### Heating Plan and Room Model (time series)

The live room forecast, every 10 minutes, read from the small `room_forecast_interval` table that octopus-app's hourly job writes ([ADR-0035](../../../.agent-docs/adr/0035-room-forecast-and-comfort-plan-come-from-a-small-learned-thermal-model.md), [ADR-0036](../../../.agent-docs/adr/0036-room-model-times-at-two-minutes-learns-at-ten-and-has-a-warm-up-delay.md)). Bars are the minutes of heating in each 10 minutes (at most 10) under the current schedule and under the comfort plan; lines on the right axis are the gas cost of each, in pounds. Points at now: the newest thermal-model fit with the mean and worst error of its backtest, and the model state; the warm-up delay of that fit in minutes with whether it was learned, carried or not learned yet; the age of the live forecast in hours.

Query A, forecast:

```sql
SELECT
  r.starts_at AS time,
  MAX(CASE WHEN r.scenario = 'current' THEN r.on_minutes END) AS current_minutes,
  MAX(CASE WHEN r.scenario = 'plan' THEN r.on_minutes END) AS plan_minutes,
  MAX(CASE WHEN r.scenario = 'current' THEN ROUND(r.cost_gbp, 3) END) AS current_cost,
  MAX(CASE WHEN r.scenario = 'plan' THEN ROUND(r.cost_gbp, 3) END) AS plan_cost
FROM room_forecast_interval r
WHERE r.kind = 'live'
  AND r.scenario IN ('current', 'plan')
  AND $__timeFilter(r.starts_at)
GROUP BY r.starts_at
ORDER BY r.starts_at;
```

Query B, model backtest:

```sql
SELECT
  LEAST(UTC_TIMESTAMP(), $__timeTo()) AS time,
  m.backtest_mean_error AS backtest_mean,
  m.backtest_worst_error AS backtest_worst
FROM thermal_model m
ORDER BY m.fitted_at DESC, m.id DESC
LIMIT 1;
```

Query C, model state:

```sql
SELECT
  LEAST(UTC_TIMESTAMP(), $__timeTo()) AS time,
  CASE
    WHEN m.usable = 1 THEN 0
    WHEN m.unusable_reason = 'too_few_steps' THEN 1
    WHEN m.unusable_reason = 'too_little_heating' THEN 2
    WHEN m.unusable_reason = 'no_fit' THEN 3
    WHEN m.unusable_reason = 'implausible' THEN 4
    WHEN m.unusable_reason = 'too_few_backtest_days' THEN 5
    WHEN m.unusable_reason = 'backtest_error_too_high' THEN 6
  END AS model_state
FROM thermal_model m
ORDER BY m.fitted_at DESC, m.id DESC
LIMIT 1;
```

Query D, forecast age:

```sql
SELECT
  LEAST(UTC_TIMESTAMP(), $__timeTo()) AS time,
  ROUND(TIMESTAMPDIFF(MINUTE, MAX(r.computed_at), UTC_TIMESTAMP()) / 60, 1) AS forecast_age_hours
FROM room_forecast_interval r
WHERE r.kind = 'live';
```

Query E, warm-up delay (the Warm-Up Delay of the newest fit, in minutes, and how it was found; one point at now, drawn like the backtest figures; the state maps 0 to "learned", 1 to "carried" and 2 to "not learned yet"):

```sql
SELECT
  LEAST(UTC_TIMESTAMP(), $__timeTo()) AS time,
  m.warm_up_delay_minutes AS warm_up_delay,
  CASE m.warm_up_delay_source WHEN 'learned' THEN 0 WHEN 'carried' THEN 1 ELSE 2 END AS warm_up_delay_state
FROM thermal_model m
ORDER BY m.fitted_at DESC, m.id DESC
LIMIT 1;
```

Not yet run against MariaDB: it is checked in the single end-of-plan dashboard review.

### Schedule Today (table)

The one table, an exception to the charts-only style. Two columns for today's London day, which changes at 00:00: "Current schedule" is the Hive schedule as programmed (every slot of the day) and "Comfort plan" is the plan's one slot, from when heating switches on (including the pre-heat) to when the comfort window closes. Each cell reads `hh:mm - hh:mm | N degrees`. Rows pair by slot position; a column with fewer slots is empty below. The last row is each scenario's estimated gas cost from the start of the 10-minute row holding now to local midnight (the live `room_forecast_interval` rows), blank when that scenario has no slot today, and shown only when priced rows cover every minute of it (judged by `TIMESTAMPDIFF(MINUTE, starts_at, ends_at)` of each row, so a weather gap or a missing gas rate blanks it rather than showing a partial total). The slots come from `room_forecast_slot`, written by the hourly room-forecast job, so deploy octopus-app before importing the dashboard that reads it.

```sql
SELECT `Current schedule`, `Comfort plan` FROM (
SELECT
  n.position AS sort_order,
  CONCAT(
    DATE_FORMAT(CONVERT_TZ(c.starts_at, 'UTC', '${timezone}'), '%H:%i'),
    ' - ',
    DATE_FORMAT(CONVERT_TZ(c.ends_at, 'UTC', '${timezone}'), '%H:%i'),
    ' | ',
    REPLACE(CAST(ROUND(c.target, 1) AS CHAR), '.0', ''),
    ' degrees'
  ) AS `Current schedule`,
  CONCAT(
    DATE_FORMAT(CONVERT_TZ(p.starts_at, 'UTC', '${timezone}'), '%H:%i'),
    ' - ',
    DATE_FORMAT(CONVERT_TZ(p.ends_at, 'UTC', '${timezone}'), '%H:%i'),
    ' | ',
    REPLACE(CAST(ROUND(p.target, 1) AS CHAR), '.0', ''),
    ' degrees'
  ) AS `Comfort plan`
FROM (SELECT position FROM room_forecast_slot WHERE scenario IN ('current', 'plan') AND day = DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}')) GROUP BY position) n
LEFT JOIN (SELECT position, starts_at, ends_at, target FROM room_forecast_slot WHERE scenario = 'current' AND day = DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}'))) c ON c.position = n.position
LEFT JOIN (SELECT position, starts_at, ends_at, target FROM room_forecast_slot WHERE scenario = 'plan' AND day = DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}'))) p ON p.position = n.position
UNION ALL
SELECT
  1000,
  (SELECT CONCAT('Cost to midnight: £', FORMAT(SUM(r.cost_gbp), 2)) FROM room_forecast_interval r WHERE r.kind = 'live' AND r.scenario = 'current' AND r.ends_at > UTC_TIMESTAMP() AND r.starts_at < CONVERT_TZ(DATE_ADD(DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}')), INTERVAL 1 DAY), '${timezone}', 'UTC') AND EXISTS (SELECT 1 FROM room_forecast_slot s WHERE s.scenario = 'current' AND s.day = DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}'))) HAVING MIN(r.starts_at) <= UTC_TIMESTAMP() AND SUM(IF(r.cost_gbp IS NULL, 0, TIMESTAMPDIFF(MINUTE, r.starts_at, LEAST(r.ends_at, CONVERT_TZ(DATE_ADD(DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}')), INTERVAL 1 DAY), '${timezone}', 'UTC'))))) = TIMESTAMPDIFF(MINUTE, MIN(r.starts_at), CONVERT_TZ(DATE_ADD(DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}')), INTERVAL 1 DAY), '${timezone}', 'UTC'))),
  (SELECT CONCAT('Cost to midnight: £', FORMAT(SUM(r.cost_gbp), 2)) FROM room_forecast_interval r WHERE r.kind = 'live' AND r.scenario = 'plan' AND r.ends_at > UTC_TIMESTAMP() AND r.starts_at < CONVERT_TZ(DATE_ADD(DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}')), INTERVAL 1 DAY), '${timezone}', 'UTC') AND EXISTS (SELECT 1 FROM room_forecast_slot s WHERE s.scenario = 'plan' AND s.day = DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}'))) HAVING MIN(r.starts_at) <= UTC_TIMESTAMP() AND SUM(IF(r.cost_gbp IS NULL, 0, TIMESTAMPDIFF(MINUTE, r.starts_at, LEAST(r.ends_at, CONVERT_TZ(DATE_ADD(DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}')), INTERVAL 1 DAY), '${timezone}', 'UTC'))))) = TIMESTAMPDIFF(MINUTE, MIN(r.starts_at), CONVERT_TZ(DATE_ADD(DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}')), INTERVAL 1 DAY), '${timezone}', 'UTC')))
) t
ORDER BY sort_order;
```

Not yet run against MariaDB: it is checked in the single end-of-plan dashboard review.

### Daily Cost: Schedules vs Actual (time series)

Three lines of daily gas cost of the heating, in pounds, for the last 30 completed London days; today is never shown. All three are heating only at the gas unit rate with no standing charge, so they are comparable and the gaps between them are about the schedule, not the bill. **Current Schedule (estimated)** and **Comfort Schedule (estimated)** are the sums of `room_forecast_interval.cost_gbp` for the `day_ahead` rows of the `current` and `plan` scenarios: the [Day-Ahead Forecast](../../../.agent-docs/context.md) as the last run before each day began left it (the job rewrites tomorrow's rows every run until midnight), never the `live` rows, which are replaced every hour. **Actual** is the sum of `heating_interval.cost_gbp` over both sources (measured and estimated), so a day whose gas has not arrived yet is still drawn from the minutes on. Off half-hours are rows with a cost of zero and count towards the day's minutes.

A line has a point for a London day only when the minutes covered by its priced rows (each row's `ends_at` less its `starts_at`) equal the minutes in that day (1,380, 1,440 or 1,500 across clock changes), so a day with a missing half-hour (too few Hive readings for `heating_interval`) or an unpriced one (no gas rate) is a gap in that line, never a partial total and never a zero. The rule is applied per line, so one line can be a gap while another is drawn. The estimated lines start with the first stored `day_ahead` rows (about 2026-11-16) and fill in one day at a time; nothing is backfilled, while Actual shows every day whose priced `heating_interval` rows cover all of its minutes (the first run rebuilds its history from the thermostat polls).

The window is fixed in the query, from London midnight 30 days ago up to, not including, London midnight today, so changing a relative dashboard time range never changes it. The panel's relative time override of 31 days only makes the axis long enough: each day's point sits at London noon, and the oldest day's noon is always less than 31 days before now. The query therefore uses no `$__timeFilter`. **Limit:** Grafana applies that override only while the dashboard range is relative. If the dashboard is zoomed or set to an absolute from and to, the override is ignored and the axis follows the selected range, so only the days inside it are drawn (the query itself still returns the same 30 days). Go back to a relative range such as the default to see the whole window. It reads only the two small tables `room_forecast_hour` (at most about 1,500 `day_ahead` rows in the window) and `heating_interval` (about 48 rows a day); until the forecast moves to `room_forecast_interval`, the forecast rows are read as one-hour spans, so it does no heavy work on the Pi.

```sql
WITH bounds AS (
  SELECT
    CONVERT_TZ(CAST(DATE_SUB(DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}')), INTERVAL 30 DAY) AS DATETIME), '${timezone}', 'UTC') AS from_utc,
    CONVERT_TZ(CAST(DATE(CONVERT_TZ(UTC_TIMESTAMP(), 'UTC', '${timezone}')) AS DATETIME), '${timezone}', 'UTC') AS to_utc
),
spans AS (
  SELECT 'current' AS series, r.starts_at, r.ends_at, r.cost_gbp
  FROM room_forecast_interval r
  CROSS JOIN bounds b
  WHERE r.kind = 'day_ahead'
    AND r.scenario = 'current'
    AND r.starts_at >= b.from_utc
    AND r.starts_at < b.to_utc

  UNION ALL

  SELECT 'plan' AS series, r.starts_at, r.ends_at, r.cost_gbp
  FROM room_forecast_interval r
  CROSS JOIN bounds b
  WHERE r.kind = 'day_ahead'
    AND r.scenario = 'plan'
    AND r.starts_at >= b.from_utc
    AND r.starts_at < b.to_utc

  UNION ALL

  SELECT 'actual' AS series, h.starts_at, h.ends_at, h.cost_gbp
  FROM heating_interval h
  CROSS JOIN bounds b
  WHERE h.starts_at >= b.from_utc
    AND h.starts_at < b.to_utc
),
daily AS (
  SELECT
    series,
    DATE(CONVERT_TZ(starts_at, 'UTC', '${timezone}')) AS day,
    SUM(cost_gbp) AS cost
  FROM spans
  GROUP BY series, DATE(CONVERT_TZ(starts_at, 'UTC', '${timezone}'))
  HAVING SUM(IF(cost_gbp IS NULL, 0, TIMESTAMPDIFF(MINUTE, starts_at, ends_at))) = TIMESTAMPDIFF(
    MINUTE,
    CONVERT_TZ(CAST(day AS DATETIME), '${timezone}', 'UTC'),
    CONVERT_TZ(CAST(day + INTERVAL 1 DAY AS DATETIME), '${timezone}', 'UTC')
  )
)
SELECT
  CONVERT_TZ(CAST(day AS DATETIME) + INTERVAL 12 HOUR, '${timezone}', 'UTC') AS time,
  MAX(CASE WHEN series = 'current' THEN ROUND(cost, 4) END) AS current_cost,
  MAX(CASE WHEN series = 'plan' THEN ROUND(cost, 4) END) AS plan_cost,
  MAX(CASE WHEN series = 'actual' THEN ROUND(cost, 4) END) AS actual_cost
FROM daily
GROUP BY day
ORDER BY day;
```

**Not yet checked against the Pi.** Checked once, read-only, on a throwaway copy of the database in the final dashboard review (never on the live MariaDB, which has a 512 MB limit). The tables are filled by octopus-app's hourly jobs, so no app deploy is needed; import the dashboard.

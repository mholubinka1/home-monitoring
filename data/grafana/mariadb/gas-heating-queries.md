# Gas, Heating and Weather Dashboard Queries

One SQL block per panel of the second dashboard, [`data/grafana/gas-heating-dashboard.json`](../gas-heating-dashboard.json) ("pi-desktop: gas-heating-monitoring", uid `gas-heating-monitoring`). It is separate from the electricity dashboard ([`electricity-dashboard.json`](../electricity-dashboard.json), documented in [`electricity-queries.md`](electricity-queries.md)), which this file does not change. Re-sync this file and the JSON together whenever the dashboard changes.

**House style.** Every panel is a chart, with no tables; the one non-chart panel is the billing-period predictor (a bar gauge). Gas, thermostat and weather panels all read the same shared database as the electricity dashboard.

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
- `${boiler_kw}`: text box, default `12`. The boiler's gas input at full output, in kW, for the estimated boost cost; 12 kW was measured on the 6 October 2026 test evening. `0` (or anything that is not a number) shows minutes only.
- `${comfort_target}`: text box, default `15` (C). A target at or above it counts as a comfort target; below it is the frost setting.
- `${comfort_margin}`: text box, default `1.0` (C). How far under its target the room must be for a minute to count.
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
```

`heating_status.polled_at` and `weather_observation.observed_at` are UTC. `weather_observation.source` is `open-meteo` for live readings and `open-meteo-archive` for backfilled history; an hour can hold both, and the live one is preferred ([ADR-0029](../../../.agent-docs/adr/0029-weather-history-backfilled-hourly-deduplicated-and-labelled-by-source.md)). A `location` is the coordinates rounded to two decimals; the current location is the one on the newest stored observation (ties on the same hour go to the newest row id), so a changed postcode never mixes two places.

---

## Row 1 — Temperatures

### Indoor, Target and Outdoor Temperature (time series)

Three lines: **indoor** (orange) and **target** (blue, drawn as a step because a target holds until changed) from the thermostat, and **outdoor** (green) from the weather, then a dashed **outdoor forecast** (green) that continues the outdoor line to the right with the forecast daily mean temperature. The gap between indoor and target is the demand for heat. There is no heating-on strip or shading. The minimum interval is 2 minutes, the poll interval, so the thermostat series shows every reading until the range is long enough that Grafana groups them (Grafana picks the interval from the panel width and the range).

Query A, thermostat (indoor is the average of the readings in each of Grafana's interval buckets; the target is the last non-NULL reading in the bucket, so a step is not blurred into an in-between value; each row is stamped at the start of its bucket, so on a long range a step can appear up to one interval early, and at the 2-minute minimum it is exact). On a long range a change that lasts less than one interval can disappear: at the default 21 days Grafana groups the readings into buckets of about 16 minutes, and the 6 October boost (23 C from 19:14 to 19:18, back to 7 C at 19:20) falls inside one bucket whose last reading is 7 C. Zoom in to see short steps; the Boost Minutes panel counts them at any range. A bucket with no reading has no row, and every line is joined across missing points (the two series have different spacing, so a gap threshold would hide one of them on a long range); a thermostat outage therefore shows as a straight line, so read it with the hive-app health in mind. `GROUP_CONCAT` is not truncated on MariaDB (its limit is 1 MB); on a MySQL server with the 1,024-byte default a very wide bucket would return a stale target:

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

---

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

**Repeated fragments.** The hourly outdoor CTE (`wx` and `daily`) is copied into the daily gas panel's query C and the band query, the Complete Gas Day condition into the two daily gas queries, and the text box number guard (`REGEXP` then `CAST`, NULL otherwise) into the boost and comfort queries, on purpose: each query stands alone in Grafana, so a later change has to be made in every copy (the tests check the copies exist).

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

### Boost Minutes and Estimated Extra Gas Cost (time series)

Bars of boost minutes per London day (boost-active readings x the 2-minute poll interval), plus a line of the estimated extra gas cost on the right axis: minutes / 60 x the **Boiler gas input** (kW, default 12, measured on the 6 October 2026 test evening) x that day's gas unit rate / 100. It is labelled "estimated; assumes full output", and a boiler input of 0 (or anything that is not a plain number, digits with an optional decimal part and no spaces) shows minutes only. Minutes assume the 2-minute poll interval hive-app uses (`HEATING_REFRESH_INTERVAL_SECONDS = 120`); if that changes, the `* 2` in the boost and comfort queries must change too. A day with thermostat readings and no boost shows 0 minutes; a day with no thermostat readings has no row.

```sql
SELECT time, boost_minutes, extra_cost_gbp
FROM (
  SELECT
    CONVERT_TZ(CAST(day AS DATETIME), '${timezone}', 'UTC') AS time,
    boost_minutes,
    IF(
      IF(${boiler_kw:sqlstring} REGEXP '^[0-9]+([.][0-9]+)?$', CAST(${boiler_kw:sqlstring} AS DECIMAL(8, 2)), NULL) > 0,
      ROUND(boost_minutes / 60 * IF(${boiler_kw:sqlstring} REGEXP '^[0-9]+([.][0-9]+)?$', CAST(${boiler_kw:sqlstring} AS DECIMAL(8, 2)), NULL) * (
        SELECT pr.unit_rate
        FROM agreement a
        JOIN product_rate pr
          ON pr.product_code = a.product_code
         AND pr.region = ${region:sqlstring}
         AND pr.valid_from <= CONVERT_TZ(CAST(day AS DATETIME), '${timezone}', 'UTC')
        WHERE a.energy = 'G'
          AND CONVERT_TZ(CAST(day AS DATETIME), '${timezone}', 'UTC') >= a.valid_from
          AND CONVERT_TZ(CAST(day AS DATETIME), '${timezone}', 'UTC') < COALESCE(a.valid_to, '9999-12-31 23:59:59')
        ORDER BY pr.valid_from DESC
        LIMIT 1
      ) / 100, 4),
      NULL
    ) AS extra_cost_gbp
  FROM (
    SELECT
      DATE(CONVERT_TZ(polled_at, 'UTC', '${timezone}')) AS day,
      COUNT(CASE WHEN boost_active = 1 THEN 1 END) * 2 AS boost_minutes
    FROM heating_status
    WHERE polled_at >= $__timeFrom() - INTERVAL 2 DAY
      AND polled_at <= $__timeTo()
    GROUP BY DATE(CONVERT_TZ(polled_at, 'UTC', '${timezone}'))
  ) b
) d
WHERE $__timeFilter(time)
ORDER BY time;
```

**Checked read-only against the Pi on 16 October 2026.** The 6 October test evening has two boost readings (19:18 and 19:24 UTC): 4 minutes, and 4 / 60 x 12 kW x 8.58396p = 6.87p (0.0687 in the query, shown as 0.07). Every other day from 3 October has 0 minutes and 0 cost. With the boiler input at 0 or `abc` the cost column is NULL and only the minutes remain.

### Minutes Below Target (time series)

Bars of minutes per London day in which a comfort target was active (target at or above the **Comfort target** variable, default 15 C) and the room was more than the **Margin below target** (default 1.0 C) under it. A day on the frost setting (7 C) counts zero whatever the room temperature, and a day with thermostat readings shows 0 rather than a gap. If a setting is not a number (a typo like `15C`), the panel shows no data instead of silently counting every frost-setting minute.

```sql
SELECT time, minutes_below_target
FROM (
  SELECT
    CONVERT_TZ(CAST(day AS DATETIME), '${timezone}', 'UTC') AS time,
    minutes_below_target
  FROM (
    SELECT
      DATE(CONVERT_TZ(polled_at, 'UTC', '${timezone}')) AS day,
      IF(
        IF(${comfort_target:sqlstring} REGEXP '^[0-9]+([.][0-9]+)?$', CAST(${comfort_target:sqlstring} AS DECIMAL(8, 2)), NULL) IS NULL OR IF(${comfort_margin:sqlstring} REGEXP '^[0-9]+([.][0-9]+)?$', CAST(${comfort_margin:sqlstring} AS DECIMAL(8, 2)), NULL) IS NULL,
        NULL,
        COUNT(CASE
          WHEN target_temp >= IF(${comfort_target:sqlstring} REGEXP '^[0-9]+([.][0-9]+)?$', CAST(${comfort_target:sqlstring} AS DECIMAL(8, 2)), NULL)
           AND current_temp < target_temp - IF(${comfort_margin:sqlstring} REGEXP '^[0-9]+([.][0-9]+)?$', CAST(${comfort_margin:sqlstring} AS DECIMAL(8, 2)), NULL)
          THEN 1 END) * 2
      ) AS minutes_below_target
    FROM heating_status
    WHERE polled_at >= $__timeFrom() - INTERVAL 2 DAY
      AND polled_at <= $__timeTo()
    GROUP BY DATE(CONVERT_TZ(polled_at, 'UTC', '${timezone}'))
  ) b
) d
WHERE $__timeFilter(time)
ORDER BY time;
```

**Checked read-only against the Pi on 16 October 2026.** 6 October: 8 minutes (the four 23 C readings of the boost evening, 19:14 to 19:18 and 19:24 UTC, with the room at 20.7 C, more than 1 C under). Every other day from 3 October shows 0, because the target never left the frost setting. With the comfort target set to `15C` every value is NULL (no data).

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

Two more queries on the same panel continue the minimum and maximum as dotted forecast lines (same colours as the observed lines, no shading), one point per forecast day from today (London) onward at London midnight like the observed days. hive-app stores one row per source and date, overwritten by each fetch, so each query reads that row; if a second source ever wrote the same date, the newest fetch wins (`ORDER BY fetched_at DESC, id DESC`; the id is text, so never `MAX(id)`). A day whose forecast has no minimum is absent from the minimum line only and still on the maximum line. Past days never appear. Daily points are one day apart, below the panel's 1.5-day `insertNulls` threshold, so consecutive forecast days join.

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

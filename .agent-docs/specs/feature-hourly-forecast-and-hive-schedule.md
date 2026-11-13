# Hourly weather forecast and the weekly Hive schedule (E1)

## Problem Statement

The household wants a forecast of the room temperature to the end of tomorrow and a comfort plan beside it ([ADR-0035](../adr/0035-room-forecast-and-comfort-plan-come-from-a-small-learned-thermal-model.md)). Two inputs that forecast needs are not stored today:

- hive-app stores only a daily **Weather Forecast** (maximum, mean, minimum). An hour-by-hour room forecast needs the outdoor temperature and the sunshine for each coming hour.
- hive-app stores only the now, next and later slots of the Hive schedule with each **Heating Status** poll. On the data of 4 to 13 November 2026 those slots reached as little as 2.5 hours ahead, so tomorrow's scheduled heating is often unknown.

This is the first of three pull requests for workstream E. It only collects data; nothing reads it yet.

## Solution

hive-app stores two new things:

- An **Hourly Weather Forecast**: Open-Meteo's forecast temperature and sunshine for the current hour and the 53 after it, refreshed by the existing hourly weather-forecast job. A later forecast for the same hour replaces the earlier one.
- The **Weekly Schedule**: Hive's full seven-day heating schedule as programmed, one row each time it changes, taken from the data each thermostat poll already downloads.

Terms are in `.agent-docs/context.md`.

## User Stories

1. As the household, I want the outdoor temperature and sunshine forecast for each coming hour stored, so that the room forecast (E3) can be run hour by hour to the end of tomorrow.
2. As the household, I want the newest forecast for an hour to replace the older one, so that the room forecast always uses the latest weather.
3. As the household, I want Hive's whole weekly schedule stored, so that the forecast knows tomorrow evening's scheduled heating even when the next three slots stop short of it.
4. As the household, I want a schedule edit picked up at the next poll, so that the forecast follows a change within two minutes.
5. As a maintainer, I want a new schedule row only when the schedule changes, so that the table stays small and keeps a history of the schedules that were in force.
6. As a maintainer, I want a poll whose weekly schedule is missing or malformed to be stored as normal without a schedule row, so that a Hive payload change never stops the thermostat polls.
7. As a maintainer, I want a malformed hourly forecast response rejected, so that a temperature is never paired with the wrong hour.
8. As a maintainer, I want the daily forecast to keep being stored when the hourly request fails, so that the heating model and cost forecast are unaffected.
9. As a maintainer, I want the glossary to record both new tables, so that the next two pull requests can rely on them.

## Implementation Decisions

**Schema (hive-app's `data/mysql/model.py`, added by Schema Sync, ADR-0005).**

- `weather_forecast_hour`: `id` (autoincrement), `source` (String 20), `location` (String 20, not null: the location key of ADR-0029), `forecast_at` (DateTime, not null, naive UTC, on the hour), `temp` (Float, C), `shortwave_radiation` (Float, W/m2), `fetched_at` (DateTime, not null). A unique index on (`source`, `location`, `forecast_at`), which the keyed upsert resolves conflicts on, as `weather_observation` does. Rows for hours that have passed are kept (24 a day); nothing prunes them.
- `heating_schedule`: `id` (autoincrement), `captured_at` (DateTime, not null, the poll time), `schedule` (JSON, not null: Hive's weekly schedule as returned, keyed by day name, each a list of slots with `start` in minutes after local midnight and `value.target`).

**Stamp convention.** `forecast_at` keeps Open-Meteo's own stamp, the same convention as `weather_observation` (ADR-0029): the temperature is the reading at the stamp, and the radiation is the mean over the hour ending at the stamp. Readers apply the convention; nothing is shifted on write.

**Open-Meteo client.** A new `get_hourly_forecast()` on `OpenMeteoClient` requests `hourly=temperature_2m,shortwave_radiation`, `forecast_hours=54` and `timezone=UTC` from the forecast endpoint. 54 covers the end of tomorrow from any time today (at most 48 hours) plus the following stamp the last hour's sunshine needs, with a margin. Checked live on 2026-11-13: the response's first stamp is the current hour. Arrays of unequal length raise `ValueError`, like the daily forecast. An hour with neither value is skipped. Each hour becomes a new `WeatherForecastHour` dataclass in `data/model.py` (`source` "open-meteo", `location`, `forecast_at`, `temp`, `shortwave_radiation`, `fetched_at`).

**Retriever.** `WeatherSource` gains `fetch_hourly_forecast` and `persist_hourly_forecast`; `WeatherApiSource` implements them. `WeatherRetriever.refresh_forecast()` fetches and persists the daily forecast first, then the hourly one, so a failing hourly request leaves the daily rows written and still fails the job (retried and recorded by the existing wrapper). No new scheduled job.

**Weekly schedule.** `HeatingStatus` gains `weekly_schedule` (a dict, or None). `HiveApiSource` reads it in the poll from the product data the library already holds (`session.data.products[<device hiveID>]["state"]["schedule"]`, the structure `getScheduleNowNextLater` itself reads); anything missing or not a dict gives None, logged at DEBUG. `persist_heating_status` writes the poll as today, then calls a new `MariaDBClient.write_weekly_schedule(captured_at, schedule)`, which inserts a row only when the schedule differs from the newest stored one (or none is stored) and does nothing for None. The comparison is of the parsed JSON, so key order does not matter.

**Docs.** The glossary's `home_monitoring` database entry lists the two new tables. ADR-0035 and the E terms are already written.

**Deploy.** Recreate hive-app on the Pi; Schema Sync creates both tables. Check after one hour: `weather_forecast_hour` has 54 rows and `heating_schedule` has one.

## Testing Decisions

Three existing seams, agreed with the user:

1. `OpenMeteoClient.get_hourly_forecast()` with stubbed HTTP (`responses`), written through `MariaDBClient` to the SQLite `mariadb_client` fixture. Prior art: `tests/test_weather_forecast_seam.py`, `tests/test_weather_forecast_minimum.py`.
2. `WeatherRetriever.refresh_forecast()` with a fake `WeatherSource`. Prior art: `tests/test_weather_retrieval.py`.
3. `HiveApiSource.fetch_heating_status()` with `FakeApyHive`, then `persist_heating_status` to the SQLite fixture. Prior art: `tests/test_heating_working_flag.py`.

Scenarios (each also a BDD red-green step):

- an hourly forecast response is stored, one row per hour with its temperature and sunshine;
- the request asks for 54 hours of the two variables in UTC;
- a second forecast for an hour already stored replaces its values and leaves one row;
- an hour with neither value is not stored;
- a response with arrays of unequal length is rejected;
- an existing database gains the `weather_forecast_hour` and `heating_schedule` tables at startup;
- the forecast refresh persists the daily forecast and then the hourly one;
- a failing hourly fetch still leaves the daily forecast persisted and fails the refresh;
- a poll carries Hive's weekly schedule;
- the first poll's weekly schedule is stored;
- an unchanged schedule on the next poll adds no row;
- a changed schedule adds a row and keeps the old one;
- a poll with a missing or malformed weekly schedule is stored and adds no schedule row.

Tests assert stored rows and returned values, not internals. Existing fakes of `WeatherSource` gain the two new methods.

## Out of Scope

- Reading either table: the thermal model (E2) and the room forecast, comfort plan and dashboard (E3).
- Other hourly variables (wind, cloud, humidity).
- Pruning `weather_forecast_hour`.
- Keeping every issued forecast for an hour; only the newest is stored.
- Any write to Hive.
- Changes to the now, next and later `schedule` column on `heating_status`.

## Further Notes

- The rest of workstream E, agreed in the design session of 2026-11-13 (ADR-0035):
  - **E2 (octopus-app):** fit the **Thermal Model** daily over 60 days of hourly steps in plain Python, **Backtest** it over the last 14 days, store both in a new `thermal_model` table; unusable above 0.5 C average error.
  - **E3 (octopus-app and dashboard):** an hourly job after the comfort-day job stores the **Room Forecast** to the end of tomorrow under the Hive schedule and under the **Comfort Plan**, with heating minutes and cost per hour, in `room_forecast_hour` (live rows replaced each run; **Day-Ahead Forecast** rows frozen at midnight). **Comfort Window** defaults in the octopus-app config: 06:30 to 00:00 on weekdays, 07:45 to 00:00 at weekends. Panel 1 gains two dotted lines; one new panel shows heating minutes per hour, costs and the backtest error.
- The weekly schedule's payload shape is taken from the library's own reader, which works on this account (now, next and later are stored today); it has not been seen raw. The first stored row is the check.
- Hive's schedule exists whatever the thermostat's mode, so a row is stored while the mode is OFF too.

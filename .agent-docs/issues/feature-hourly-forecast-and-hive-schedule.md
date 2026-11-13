# Issues: feature-hourly-forecast-and-hive-schedule

## Hourly weather forecast stored

**GitHub issue**: #764

**Blocked by**: None

**User stories**: 1, 2, 7, 8

### What to build

hive-app stores Open-Meteo's forecast temperature and sunshine for the current hour and the 53 after it in a new `weather_forecast_hour` table, one row per source, location and hour, the newest forecast replacing the older one. The existing hourly weather-forecast job fetches and persists the daily forecast first and then the hourly one, so a failing hourly request leaves the daily rows written and still fails the job. Stamps are kept as Open-Meteo gives them, in UTC. Part of workstream E (ADR-0035); nothing reads the table yet.

### Acceptance criteria

- [x] Given an Open-Meteo hourly response of three hours, when the forecast is fetched and written, then `weather_forecast_hour` has three rows with each hour's temperature and sunshine.
- [x] Given the hourly forecast is requested, then the request asks for 54 hours of temperature and shortwave radiation in UTC.
- [x] Given a stored forecast for an hour, when a later forecast for that hour is written, then the hour has one row with the later values.
- [x] Given a response hour with neither a temperature nor a sunshine value, when it is fetched, then that hour is not stored.
- [x] Given a response whose arrays differ in length, when it is fetched, then it is rejected with an error naming the mismatch.
- [x] Given a database created before this change, when hive-app starts, then `weather_forecast_hour` exists.
- [x] Given a working source, when the forecast refresh runs, then the daily forecast is persisted and then the hourly one.
- [x] Given the hourly fetch fails, when the forecast refresh runs, then the daily forecast is still persisted and the refresh fails.

---

## Weekly Hive schedule stored on change

**GitHub issue**: #765

**Blocked by**: None

**User stories**: 3, 4, 5, 6, 9

### What to build

Each thermostat poll also carries Hive's full seven-day schedule, read from the product data the Hive library already holds. hive-app stores it in a new `heating_schedule` table, adding a row only when it differs from the newest stored one, so the table keeps a short history of the schedules that were in force. A poll whose weekly schedule is missing or malformed is stored as normal and adds no schedule row. The glossary's database entry lists both new tables. Part of workstream E (ADR-0035); nothing reads the table yet.

### Acceptance criteria

- [x] Given Hive's product data holds a weekly schedule, when the thermostat is polled, then the poll carries it.
- [x] Given no stored schedule, when a poll with a weekly schedule is persisted, then `heating_schedule` has one row with it and the poll's time.
- [x] Given a stored schedule, when the next poll carries the same schedule, then no row is added.
- [x] Given a stored schedule, when a poll carries a different one, then a second row is added and the first is kept.
- [x] Given a poll whose weekly schedule is missing or not a dictionary, when it is persisted, then the `heating_status` row is written and no schedule row is added.
- [x] Given a database created before this change, when hive-app starts, then `heating_schedule` exists.

---

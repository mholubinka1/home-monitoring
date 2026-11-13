# Thermal model, fitted daily and backtested (E2)

## Problem Statement

The household wants a forecast of the room temperature to the end of tomorrow and a comfort plan beside it ([ADR-0035](../adr/0035-room-forecast-and-comfort-plan-come-from-a-small-learned-thermal-model.md)). Both need a description of how the room warms and cools, learned from the thermostat's own history, and a measure of how far that description can be trusted. Neither exists yet.

This is the second of three pull requests for workstream E. E1 (merged) stores the hourly weather forecast and the weekly Hive schedule. This one learns and stores the **Thermal Model** and its **Backtest**; nothing is forecast or shown yet (E3).

## Solution

Once a day octopus-app fits a **Thermal Model**: five numbers that say how the room's temperature changes over an hour.

- how much it falls for each degree the room is above outdoors;
- how much it rises per hour of heating;
- how much of that rise arrives in the following hour;
- how much sunshine adds;
- a constant gain from people and appliances.

Every fit is **backtested**: each of the last 14 days is replayed, hour by hour from its midnight reading, by a model fitted only on the data before that day, using the weather and heating that really happened. The average and the worst error are stored with the model. A model is marked unusable, with a reason, when there is too little data, when the numbers make no physical sense, or when the average backtest error is above 0.5 C.

Terms are in `.agent-docs/context.md`.

## User Stories

1. As the household, I want the thermal model learned from my own thermostat, heating and weather history, so that the forecast describes this house and not a typical one.
2. As the household, I want the model relearned every day from the last 60 days, so that it follows the house as winter sets in.
3. As the household, I want every relearn backtested on the last 14 days by models that had not seen the day they predict, so that the stored error is an honest measure.
4. As the household, I want a model marked unusable when its average backtest error is above 0.5 C, so that a poor forecast is never drawn.
5. As the household, I want a model marked unusable when it has too little data or too little heating to learn from, or when its numbers make no physical sense, so that a guess is never presented as learned.
6. As a maintainer, I want every fit stored with its numbers, its backtest figures and, when unusable, the reason, so that I can see why the dashboard is silent.
7. As a maintainer, I want an hour used only when its readings are complete, so that a Hive outage or a weather gap is left out and never filled in.
8. As a maintainer, I want the relearn to run in the hourly job chain, after the heating hours it reads and only when they refreshed, with its own job record, so that it never learns from a half-written table and its failures are visible.
9. As a maintainer, I want the model explained in plain language in the heating guide, so that it can be understood and challenged later.

## Implementation Decisions

**Schema.** A new SQLAlchemy model `thermal_model` in octopus-app's `data/mysql/model.py`, added by Schema Sync (additive, ADR-0005), one row per fit, never pruned (365 rows a year):

- `id` (autoincrement), `fitted_at` (DateTime, not null)
- `window_start`, `window_end` (Date, not null): the local days the fit's hours span
- `usable` (Boolean, not null), `unusable_reason` (String 100)
- `steps_used`, `heating_steps` (Integer)
- `loss_per_degree`, `heating_gain`, `carried_gain`, `sun_gain`, `constant_gain` (double, nullable)
- `backtest_days` (Integer), `backtest_mean_error`, `backtest_worst_error` (double, nullable)

A matching frozen `ThermalModelRecord` dataclass and a `StoredThermalModel` (id plus record) sit beside the heating-model ones in `data/model.py`.

**An hourly step.** The unit of evidence is one UTC hour `h`:

- *Room at an hour's start*: the first thermostat reading at or after the hour's start and less than 10 minutes into it. No such reading, or one with no temperature, means the hour has no start temperature.
- A step exists for `h` when the room has a start temperature at `h` and at `h + 1`, `heating_hour` rows are stored for `h` and for `h - 1` (they carry the minutes the heating was on and already exclude hours with too few polls, ADR-0033), the outdoor temperature is stored at stamp `h`, and the radiation at stamp `h + 1` (Open-Meteo stamps an hour's mean radiation at its end, ADR-0029).
- Its inputs: the gap `outdoor - room`, the heating fraction `on_minutes / 60` of `h`, the same fraction of `h - 1`, the radiation in kW/m2, and a constant 1. Its outcome: the room's change over the hour.

**The model.** `change = loss_per_degree * (outdoor - room) + heating_gain * heating + carried_gain * previous heating + sun_gain * radiation + constant_gain`, fitted by ordinary least squares over the steps of the 60 days before the fit. octopus-app has no numerical library and gains none: the five-by-five normal equations are solved in plain Python by Gaussian elimination with partial pivoting. This and the replay are pure functions in a new `data/thermal.py`, with named module constants like those in `data/heating_hours.py`.

**Usability**, checked in this order; the first that fails is the stored reason:

1. `too_few_steps`: under 168 steps (a week of hours).
2. `too_little_heating`: under 10 steps with at least 10 minutes of heating.
3. `no_fit`: the equations cannot be solved (inputs that do not vary).
4. `implausible`: the loss or the heating gain is not above zero.
5. `too_few_backtest_days`: under 5 days could be backtested.
6. `backtest_error_too_high`: the backtest's mean error is above 0.5 C.

**Backtest.** For each of the 14 local days before the day of the fit:

- The day is replayed only when every one of its hours (23, 24 or 25 of them) is a step, and a model fitted on the 60 days before that day passes checks 1 to 4. Otherwise the day is skipped whole.
- The replay starts from the room's start temperature at the day's first hour and applies the model hour by hour, feeding its own prediction forward, with the real outdoor temperature, radiation and heating minutes.
- Each hour's error is the predicted room at the hour's end against the room's start temperature of the next hour.

The stored mean error is the mean of the absolute errors over every hour of every replayed day; the worst error is their maximum; `backtest_days` is the number of days replayed.

**Job.** A new `ThermalModelJob` in `data/thermal_job.py`, in the style of `ComfortDayJob` (a class with `refresh(now)` taking the clock). A run does nothing when the newest stored fit was made on today's local day; otherwise it reads 74 days of inputs once (60 for the earliest backtest fit plus 14), fits, backtests and writes one row, usable or not. With no thermostat readings at all, or no hourly weather at all (hive-app has not run), it writes nothing. The newest row is the model in force: when it is unusable, nothing is forecast until the next day's fit (unlike the heating model, an older usable fit is not reused, ADR-0035).

**Client (`MariaDBClient` in `data/mysql/client.py`).** New: a read of the hourly outdoor temperature and radiation by stamp in a UTC range for the current location, the live reading winning over the archive's and a missing table reading as no rows (the rule `read_complete_day_mean_temperatures` already applies, shared and not copied); `write_thermal_model`; `read_latest_thermal_model`. The room readings and heating hours use the existing `read_room_temperatures` and `read_heating_hours`.

**Scheduling.** `register_heating_hour_job` gains the thermal job: after a successful heating-hour run the comfort days are refreshed and then the thermal model, each recorded as its own `job_run` (new name `thermal_model_refresh`); after a failed heating-hour run both are recorded as skipped. The thermal step does not depend on the comfort step's outcome. Running in the hourly chain gives the first fit within an hour of deploy and a refit on the first run of each local day.

**Docs.** `docs/heating-model.md` gets a plain-language section on the thermal model and its backtest, with glossary entries. The glossary's database entry lists `thermal_model`. ADR-0035 and the terms are already written.

## Testing Decisions

One seam, agreed with the user: `ThermalModelJob.refresh(now)` run against the existing SQLite `mariadb_client` fixture, as `tests/test_comfort_day_job.py` does. Tests seed `heating_status` readings, `heating_hour` rows and `weather_observation` rows generated from known model numbers, run the job with a fixed clock, and assert the stored `thermal_model` row. No separate unit tests of the fit or the replay.

Scenarios, one per behaviour (each also a BDD red-green step):

- history generated from known numbers gives a stored model with those numbers;
- an hour with no reading in its first 10 minutes, with no stored heating hour, or with no weather is left out, and the model is still recovered;
- an hour starts from its first reading: a later, different reading changes nothing, a first reading just inside 10 minutes counts, and one at exactly 10 minutes or with no temperature leaves the hour out;
- under a week of steps gives an unusable `too_few_steps` row;
- a history with almost no heating gives `too_little_heating`;
- a history whose room never changes and whose inputs do not vary gives `no_fit`;
- a history in which heating cools the room gives `implausible`;
- a second run on the same local day writes nothing, and the first run of the next day writes a new row;
- no thermostat readings, or a missing `heating_status` or `weather_observation` table, writes nothing and does not raise;
- a history that follows the model exactly gives a backtest over 14 days with a mean and worst error near zero and a usable row;
- a backtest day with a gap in its hours is skipped, and the count of days says so;
- fewer than 5 backtestable days gives `too_few_backtest_days`, and exactly 5 gives a usable row;
- a backtest that spans a clock change replays the 25-hour day like any other;
- a history whose last fortnight behaves differently from what went before gives a mean error above 0.5 C and `backtest_error_too_high`;
- a backtest day is replayed by a model that has not seen it: changing that day's own readings changes its error but not the numbers of the model that replayed it.

Also: the hourly chain cases (the thermal job runs after a successful heating-hour run with its own `job_run`, is recorded as skipped after a failed one, and still runs after a failed comfort refresh) live in a new `tests/test_hourly_heating_chain.py`, with the comfort job's chain cases moved there from `tests/test_refresh_scheduling.py`, which had reached pylint's 1000-line module limit. Prior art: `tests/test_comfort_day_job.py`, `tests/test_heating_hour_job.py`, `tests/test_refresh_scheduling.py`.

Outside the test suite, the finished code is run once over the readings of 3 October to 13 November 2026 pulled during the design session, and the numbers recorded in the pull request.

## Out of Scope

- The room forecast, the comfort plan, the comfort-window config and the dashboard (E3).
- Reading `weather_forecast_hour` or `heating_schedule` (E3).
- A two-layer model, wind, or any term beyond the five.
- Choosing between model forms by backtest.
- Reusing an older usable model when the newest is unusable.
- Pruning `thermal_model`.
- Changes to the heating model, the heating hours or the comfort days.

## Further Notes

- Design-session prototype on 3 October to 13 November 2026 (35 days, 30 heating runs): about 0.02 C lost per hour per degree, 1.3 C gained per hour of heating plus 0.3 C in the next hour; fitted before 5 November and run over 5 to 13 November, mean error 0.30 C, worst 0.86 C. The prototype used a looser hour definition, so the built figures will differ a little.
- Thin evidence: about 16 days with heating, nothing colder than 0.7 C outdoors, and no thermostat data for 21 to 27 October. Early backtest days may be skipped for too little heating before them; the 5-day minimum is what that guards.
- The live `weather_observation` rows can carry a NULL radiation; such an hour is not a step.
- If hive-app's poll interval changes, the 10-minute start tolerance and `heating_hour`'s poll threshold change with it (ADR-0033).
- E3 must allow a NULL radiation in `weather_forecast_hour` (a later forecast can overwrite a value with NULL).

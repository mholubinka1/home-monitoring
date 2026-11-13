---
status: accepted
---

# Room forecast and comfort plan come from a small learned thermal model

The household wants to see what the room will do tomorrow and what heating would keep it comfortable, as advice only. We decided octopus-app learns a **thermal model** of five numbers from the thermostat's own history and runs it forward hour by hour: the heat lost per hour for each degree the room is above outdoors, the heat gained per hour of heating, the part of that gain that arrives in the following hour, the gain from sunshine and a constant gain (people and appliances). It is fitted by least squares over the last 60 days of hourly steps and refitted once a day. Every refit is **backtested**: each of the last 14 days is replayed from its midnight reading by a model fitted only on the data before it, using the weather and heating that really happened, and the average and worst error are stored with the model. A model is unusable, and nothing is forecast, when the average error is above 0.5 C, when the loss or the heating gain is not positive, or when there is too little heating in the window.

An hourly job then stores a **room forecast** to the end of tomorrow, twice: under the Hive schedule as programmed, and under a **comfort plan**, which holds the comfort temperature (rounded up to the half degree Hive accepts) inside a configured **comfort window** (06:30 to midnight on weekdays, 07:45 to midnight at weekends) with the shortest pre-heat that reaches it by the window's start. Each has its heating minutes and cost per hour. Live rows are replaced on every run; **day-ahead** rows are written only while their day is still tomorrow, so they freeze at midnight and past days show a true day-ahead forecast against what happened. Nothing is written to Hive.

hive-app supplies two new inputs: the hourly weather forecast (temperature and sunshine) and Hive's full weekly schedule, stored one row per change.

On the data of 3 October to 13 November 2026 (35 days, 30 heating runs, about 16 days with heating) the fitted numbers were about 0.02 C lost per hour per degree, 1.3 C gained per hour of heating plus 0.3 C in the next hour. Fitted before 5 November and run over 5 to 13 November, the average error over 24 hours was 0.30 C (worst 0.86 C); "the room stays where it is" scored 0.45 C.

## Considered Options

- **Forecast only, or a look back at what each heating run bought.** Smaller, but neither says what to change. The household chose forecast plus advice.
- **Advice that only moves the existing slots earlier, or a fixed menu of three options.** The first never questions which hours are heated; the second puts more on the dashboard to read. One plan beside the current schedule was chosen.
- **The plan aims at counted hours (ADR-0034).** It would match the Minutes Below Comfort panel with no setting, but one late boost would make the plan heat at 01:00 for a year and unheated hours such as 09:00 to 11:00 would never be planned. The household chose a fixed window per day type, in the app config.
- **No sunshine term.** Simpler, but the out-of-sample error was 0.37 C against 0.30 C. Sunshine is used in the model and still never shown on the dashboard.
- **Letting the backtest choose between model forms at each refit.** The model could flip from week to week, which is harder to follow.
- **A two-layer model (air and walls).** More faithful to a slow house, but six weeks of data cannot pin down its extra numbers. The carry-over term stands in for the radiators' lag.
- **Rebuilding the weekly schedule from the stored now/next/later slots.** No hive-app change, but an edited schedule could take a week to show, which is when the advice is being checked.
- **A backtest run once and written up.** The model would drift unseen as winter sets in; the gate needs a fresh figure at every refit.
- **One forecast per hour, updated until the hour passes.** A past hour would then show a forecast made minutes before it, and scoring it would flatter the model. Hence the frozen day-ahead rows beside the live ones.
- **A stored score per day for the live forecasts.** Not built: the day-ahead line is drawn against the measured room instead. A day with an unplanned boost reads as a miss, which the backtest (real heating replayed) does not suffer from.
- **Seven or three days ahead.** The error compounds (about 0.2 C at 6 hours, 0.4 C at 24) and the weather forecast weakens. The forecast stops at the end of tomorrow.

## Consequences

- Thin evidence: nothing colder than 0.7 C outdoors has been seen and a week (21 to 27 October) has no thermostat data. The daily refit and the gate are what keep a poor model off the dashboard.
- Holding a thermostat target is the least heating that keeps a single room at a temperature, so the plan needs no optimiser: only the pre-heat length is searched.
- When Hive's mode is OFF the current-schedule forecast assumes no heating. A boost running at forecast time is ignored.
- The plan is silent on days with no comfort temperature (ADR-0034); the current-schedule forecast is still drawn.
- Nothing new is stored when there is no usable model, no hourly weather forecast for the hours ahead, or the newest thermostat reading is more than 30 minutes old.
- Cost uses the learned boiler kW and the gas unit rate of ADR-0033, so it carries the same caveat: thermostat demand is not proof the burner fired throughout.
- The thermostat is one room; the forecast and the plan are that room's.
- If hive-app's poll interval changes, the hourly steps' poll thresholds change with it (as for ADR-0033).

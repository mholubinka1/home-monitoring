# Gas dashboard review tweaks

## Problem Statement

After using the gas/heating dashboard the user found two things wrong: the forecast outdoor temperature sits on the daily gas panel, away from the temperatures it belongs with, and the monthly gas chart shows two sets of bars plus complete-day dots where the electricity dashboard shows a single year-on-year ratio.

## Solution

1. The forecast mean temperature moves from "Daily Gas and Outdoor Temperature" to "Indoor, Target and Outdoor Temperature". It continues the outdoor line to the right as a dashed line of daily means. A forecast day only draws where no outdoor reading exists yet, so each actual reading replaces the forecast as it arrives (the same actual-then-forecast idea as the electricity Agile price panel).
2. "Monthly Gas" becomes a year-on-year percentage change per month for the last 12 months. Each month is first scaled to its full length from its complete gas days (as today), so a part-filled month compares fairly. The days-counted dots and their axis are removed.

## User Stories

1. As the householder, I want the forecast outdoor temperature on the temperatures panel, so that I can see where the weather is heading next to indoor and target.
2. As the householder, I want a forecast day to disappear once real readings cover it, so that the panel shows actuals wherever they exist.
3. As the householder, I want the daily gas panel to show only gas and the actual outdoor mean, so that it is not cluttered by the forecast.
4. As the householder, I want monthly gas as a year-on-year ratio, so that I can compare with the electricity panel.
5. As the householder, I want each month scaled to its full number of days and no day counts shown, so that a half-finished month does not look like a drop.

## Implementation Decisions

- Panel 1 gets query C, `outdoor_forecast`: one row per forecast day (the newest fetch for each date across sources, rows with a mean only, today or later), stamped at 12:00 London on the target date, kept only if later than the newest outdoor reading of the current location. A second point at the newest actual reading (same value as the last actual) is included so the dashed line joins the solid one. Drawn dashed, same green as outdoor.
- Panel 2 loses query D, the `forecast_mean` override and the forecast sentences in its description.
- Dashboard range is unchanged (`now-14d` to `now+7d`).
- Panel 7 query: keep the scaled monthly figure, compute `yoy_pct_change` = (month - same month a year earlier) / same month a year earlier x 100, for the last 12 months; a month lacking a usable comparator (under 10 complete days either year) is left out. Bars, unit percent, no days series. The title keeps the prefix "Monthly Gas".
- No schema change. Electricity dashboard files are not touched.

## Testing Decisions

- Structure tests in the dashboard test module (JSON-level), as for the other panels: forecast query lives on panel 1 and not panel 2; forecast query is hidden by actuals; the monthly panel has a percentage series and no days series.
- SQL checked read-only on the Pi with a known month and a known forecast day; evidence recorded in the queries document.

## Out of Scope

- Wave 2 panels (#640 to #645), paused until the user finishes evaluating.
- An hourly forecast (the Pi stores daily forecast means only).

## Further Notes

Real-Grafana look at both panels is the user's step.

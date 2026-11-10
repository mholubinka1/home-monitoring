> Work complete — PR ready to merge.

# Issues: feature-outdoor-band-rework

## Store the daily forecast minimum temperature

**Blocked by**: None

**User stories**: 9, 12

### What to build

hive-app's daily forecast fetch also asks Open-Meteo for each day's minimum temperature and stores it beside the maximum and mean in a new nullable `min_temp` column on the Weather Forecast table. Schema Sync adds the column to an existing table without a manual step, keeping existing rows with a null minimum. A response without minimums stores nulls rather than failing.

### Acceptance criteria

- [x] Given a forecast response with minimums, when it is fetched and written, then each day's minimum is stored beside its maximum and mean
- [x] Given a forecast response with no minimum array, when it is written, then the minimums are stored as null and the maximum and mean are unaffected
- [x] Given a minimum array of a different length from the days, when the forecast is fetched, then it is rejected
- [x] Given an existing `weather_forecast` table without `min_temp`, when hive-app starts, then the column is added and existing rows keep their data with a null minimum

---

## Panel 5: observed min and max plus Waking-Hours Indoor Average

**Blocked by**: None

**User stories**: 1, 2, 3, 4, 5, 10, 11

### What to build

The Daily Outdoor Temperature Band shows the observed daily outdoor minimum and maximum (complete London days) and a new Waking-Hours Indoor Average line. Two new dashboard variables, Wake Up (default 7) and Bedtime (default 23), set the window; a day is plotted only if at least 80% of the expected polls in the window exist. The mean leaves this panel and stays on the Daily Gas chart, which is unchanged. Panel title and description, and the panel queries doc, are updated. Sunshine and solar radiation remain unused.

### Acceptance criteria

- [x] Given the dashboard, when it loads, then Wake Up and Bedtime variables exist with defaults 7 and 23 and every variable used by a panel is defined
- [x] Given panel 5, when inspected, then it has observed minimum, maximum and indoor average series and no mean series
- [x] Given a day with exactly 80% of the expected polls in the window, when plotted, then the day is shown; given fewer, then it is a gap
- [x] Given panel 2, when inspected, then its outdoor mean line is unchanged
- [x] Given a throwaway copy of the data, when panel 5's SQL runs by hand, then the indoor average matches a hand-calculated day (never run on the live database)
- [x] Panel text and `gas-heating-queries.md` describe the new panel

---

## Panel 5: dotted forecast minimum and maximum

**Blocked by**: #747, #748

**User stories**: 6, 7, 8, 9

### What to build

Panel 5 adds the forecast minimum and maximum for today and the coming days as dotted lines in the colours of the observed ones, using the latest stored forecast for each day. Days whose stored forecast has no minimum are omitted from the minimum line, and past days never show forecast data.

### Acceptance criteria

- [x] Given panel 5, when inspected, then forecast minimum and maximum series exist and are drawn dotted
- [x] Given forecasts fetched more than once for a day, when plotted, then the latest fetch is used
- [x] Given a forecast row with a null minimum, when plotted, then that day is absent from the minimum line but present on the maximum line
- [x] Given today's date, when plotted, then today and later days appear and earlier days do not
- [x] Given a throwaway copy of the data, when the forecast SQL runs by hand, then it returns the expected rows (never on the live database)
- [x] `gas-heating-queries.md` documents the forecast series

---

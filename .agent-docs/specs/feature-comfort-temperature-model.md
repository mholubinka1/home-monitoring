# Comfort temperature model

## Problem Statement

The gas dashboard's fourth panel, "Minutes Below Target", counts a minute only while a schedule target is active and the room is more than a typed margin under it. The thermostat target sits at the 7 C frost setting except for a short morning schedule, so the panel shows 0 almost every day and says nothing about when the house felt cold. The two text boxes behind it (`comfort_target`, default 15, and `comfort_margin`, default 1.0) are guesses the household has to maintain.

The household wants a learned comfort temperature, which is not the thermostat target, drawn on the temperature panel and used to count the minutes the room was below it.

## Solution

octopus-app learns, for each London day, a **Comfort Temperature**: the median room temperature at the moment the household pressed boost (a **Boost Start**), over the 90 days before that day. It also learns the day's **Counted Hours**: the London clock hours in which the heating was on at least once in the same 90 days. An hourly job stores one **Comfort Day** row per London day with the comfort temperature, the counted hours and the minutes the room was below comfort within them.

- Panel 1 gains a stepped "comfort" line read from the table.
- Panel 4 becomes "Minutes Below Comfort", bars read from the table.
- The `comfort_target` and `comfort_margin` text boxes and the old panel query are deleted.
- A finished day is settled once and never restated. Before a comfort temperature exists (fewer than 5 boosts ever seen in a window), the line and the bar are absent, not zero.

Decisions are in [ADR-0034](../adr/0034-comfort-temperature-is-learned-from-the-room-temperature-at-boost-starts.md). Terms are in `.agent-docs/context.md`.

## User Stories

1. As the household, I want the comfort temperature learned from the room temperature at which I press boost, so that it reflects when I actually feel cold and not what the thermostat is set to.
2. As the household, I want the comfort temperature to follow my habits over the last 90 days, so that it moves with me through the winter.
3. As the household, I want the last learned comfort temperature to carry on when there are too few recent boosts (for example after summer), so that the line does not vanish or jump to a made-up default.
4. As the household, I want nothing shown until at least 5 boosts exist, so that the dashboard never presents a guess as learned.
5. As the household, I want a boost that I extend or re-press within 10 minutes counted as one boost, so that one cold evening is one piece of evidence.
6. As the household, I want minutes counted as below comfort only in clock hours in which I have heated in the last 90 days, so that cold nights while asleep do not swamp the figure.
7. As the household, I want a finished day's figures never to change, so that numbers I saw last week stay the same and I can see my comfort level drift.
8. As the household, I want today's figures updated every hour, so that the panel is current.
9. As the household, I want a comfort line on the temperature panel, so that I can see the room against the level I am content with.
10. As the household, I want the minutes panel to show minutes below comfort and the two typed settings removed, so that the dashboard has no control left to maintain for this.
11. As a maintainer, I want the panels to read one small stored row per day, so that the Pi's database (512 MB cap) is not asked to scan the 2-minute readings on every refresh.
12. As a maintainer, I want the decision, glossary, queries document and explainer updated with the code, so that the model can be understood and challenged later.

## Implementation Decisions

**Schema.** A new SQLAlchemy model `comfort_day` in octopus-app's `data/mysql/model.py`, added by Schema Sync (additive, ADR-0005). It is never pruned (365 rows a year). Columns:

- `date` (Date, the London day, primary key)
- `comfort_temp` (nullable): the comfort temperature used for the day, in C
- `boost_starts` (Integer): the number of boost starts in the day's learning window
- `counted_hours` (String): the day's counted London hours of day, ascending and comma-separated (for example `0,1,6,7,12`); empty when there are none
- `minutes_below` (nullable Integer): minutes the room was below comfort in counted hours; NULL when the day has no comfort temperature
- `computed_at`

A matching `ComfortDayRecord` dataclass sits beside `HeatingHourRecord` in `data/model.py`.

**Learning (pure functions in a new `data/comfort.py`).** For a London day whose start is `day_start` (UTC), the learning window is `[day_start - 90 days, day_start)`, so a day is never judged by its own boosts.

- *Boost starts.* Among the polls with `boost_active = 1`, in time order, a poll is a boost start when it is more than 10 minutes after the previous boost poll (or is the first one read). A boost start counts for a window when its time falls inside it. Its `current_temp` is the evidence; a boost start with no room temperature is ignored.
- *Comfort temperature.* The median of the boost-start room temperatures when there are at least 5. With fewer, the newest stored `comfort_temp` from an earlier day carries on; with none stored, the day has no comfort temperature. No margin is applied.
- *Counted hours.* The London hours of day of the stored `heating_hour` rows in the window with `on_minutes > 0` (ADR-0033's heating-on definition, `state='ON' OR boost_active=1`, is already applied there). The UTC hour is converted to the London hour of day, so the hours follow the clock change.
- *Minutes below comfort.* `2 x` the day's polls whose London hour of day is a counted hour and whose `current_temp` is below the comfort temperature (strictly). Polls with no room temperature are skipped. The 2 is `heating_hours.POLL_MINUTES`.

The constants (90 days, 5 boosts, 10 minutes) are named module constants, like those in `data/heating_hours.py`.

**Job.** A new `ComfortDayJob` in `data/comfort_job.py`, in the style of `HeatingHourJob` (a class with `refresh(now)` taking the clock). Each run computes the London days from the newest stored `comfort_day` date (inclusive) to today (inclusive), oldest first, and writes their rows (an upsert by date, so a run never deletes a row). On an empty table it starts from the London day of the first `heating_status` poll. When the newest stored day was last written after it had ended, it is already settled and the run starts from the day after it, so a settled day is not rewritten even while no later day has polls (a Hive outage). So today's row is rewritten every hour, a finished day is rewritten one last time on the first run after it ends, and older days are never touched. The boost polls are read from 10 minutes before the earliest window, so a boost already running as a window opens is not taken for a new one. A day with no polls gets no row. The run reads the boost polls and heating-on hours once for the whole span and slices them per day; each day's polls are read a day at a time.

**Client (`MariaDBClient` in `data/mysql/client.py`).** New reads on the existing hand-mirrored `heating_status` table, each treating a missing table as no rows like `read_heating_polls`: boost polls `(polled_at, current_temp)` in a UTC range, and room temperatures `(polled_at, current_temp)` in a UTC range. New `comfort_day` methods: the newest stored date, the newest stored comfort temperature before a date, the start hours of `heating_hour` rows with `on_minutes > 0` in a range, and an upsert of rows by date. The London hour of day of a UTC time comes from a new `to_local_hour` beside `to_local_date` in the shared `local_day` module, which `data/heating_hours.py` then uses in place of its private copy.

**Scheduling.** The comfort job runs in the same hourly scheduled function as the heating-hour job, straight after it, and only when the heating-hour run succeeded. This is because counted hours are read from `heating_hour`, and a day frozen while `heating_hour` was still empty or mid-backfill would be frozen wrong. It follows the daily chain's pattern in `main.py`: each step is recorded as its own `job_run` (new name `comfort_day_refresh`), and a step skipped because its predecessor failed is recorded as skipped.

**Dashboard (`data/grafana/gas-heating-dashboard.json`).**

- Panel 1 gains a query D returning `time, comfort` from `comfort_day`: one point per day at London midnight (converted to UTC with `${timezone}`, like the other daily panels), plus one closing point for the newest day that starts within the selected range, at the end of that day or the end of the range if that is sooner, so the step reaches the right-hand edge whether the range ends now, in the past or in the future. The series is drawn `stepAfter` in its own colour, named "comfort". The panel description says what the line is.
- Panel 4 is retitled "Minutes Below Comfort" and its query replaced by a read of `minutes_below` from `comfort_day` (same London-midnight stamp, alias `minutes_below_comfort`, display name "minutes below comfort", fixed `min` suffix kept). A day with no comfort temperature is a gap. The description is rewritten.
- The `comfort_target` and `comfort_margin` variables are removed. `wake_up` and `bedtime` (used by the outdoor band's indoor average) are untouched.
- The dashboard is imported, so deployment order is the app first, then the import; the live Grafana copy may have been edited, so re-export before applying the JSON change.

**Docs.** `data/grafana/mariadb/gas-heating-queries.md` drops the two variables and the old comfort query (and its mention in the "Repeated fragments" note), adds `comfort_day` to the schema block, the panel 1 comfort query and the new panel 4 section. `docs/heating-model.md` gets a plain-language section on the comfort temperature. ADR-0034 and the glossary terms (Comfort Temperature, Boost Start, Counted Hour, Comfort Day) are already written.

## Testing Decisions

One seam, the highest: `ComfortDayJob.refresh(now)` run against the existing SQLite `mariadb_client` fixture, as `tests/test_heating_hour_job.py` does for the heating-hour job. Tests seed `heating_status` polls (through the mirrored table) and `heating_hour` rows, run the job with a fixed clock, and assert the `comfort_day` rows. They test behaviour, not the internals of the learners. No separate unit tests for the pure functions.

Scenarios, one per behaviour (each also the BDD red-green step):

- five boosts at known room temperatures before a day give that day the median as its comfort temperature;
- a boost on the day itself is not evidence for that day;
- boost polls within 10 minutes of each other are one boost start, and a gap of more than 10 minutes starts a new one;
- a boost older than 90 days is not used;
- with fewer than 5 boosts in the window the newest earlier stored comfort temperature carries on, and with none stored the day has no comfort temperature and NULL minutes;
- minutes below comfort count only polls in counted hours with the room strictly below comfort;
- a clock hour heated only before the 90-day window is not counted;
- on a clock-change day the counted hours follow London time;
- today's row is rewritten on each run, yesterday's is settled on the first run after midnight, and an older day is never rewritten even when its evidence changes;
- the first run on an empty table fills every day back to the first poll, each from its own earlier evidence;
- a day with no polls gets no row;
- a missing `heating_status` table writes nothing and does not raise.

Also: `tests/test_grafana_dashboards.py` is updated: the comfort line is a step read from `comfort_day`, panel 4 reads `minutes_below` from `comfort_day`, the two variables are gone, and the tests of the old comfort query (number guard, shared-fragment copy, defaults) are removed or rewritten. A test for the hourly chain follows the existing registration tests in `tests/test_refresh_scheduling.py`: the comfort job runs after a successful heating-hour run and is recorded as skipped after a failed one. The panel queries are checked once, in the final dashboard review, on a throwaway database copy (never on the live MariaDB). Prior art: `tests/test_heating_hour_job.py`, `tests/test_refresh_scheduling.py`.

## Out of Scope

- A comfort temperature per time band; one number per day (ADR-0034 records bands as the next step).
- Manual raises or schedule changes as evidence.
- A dashboard override for the comfort temperature.
- Any dependence of comfort on outdoor temperature.
- The thermal model and schedule advice (E), the cost-based gas panel (F) and the flow-temperature model (G).
- Changes to `heating_hour`, the heating model, the cost forecast, or panels 2, 3, 5 and 6.
- Pruning `comfort_day`.

## Further Notes

- On the data of 3 October to 12 November 2026 there were 13 boosts; the first day with 5 earlier boosts is 5 November, so earlier days show no line and no bar. The median then was about 17 C.
- The 2026-10-06 test boost (room 20.7 C) is not excluded; a median absorbs it and it leaves the window in January 2027.
- By construction about half of boosts start above the comfort temperature.
- A frozen day cannot be repaired by a later run. If a day is ever settled wrongly (for example a bug), the fix is to delete its row and the rows after it by hand; the next run refills from the newest remaining date.
- Counted hours are patchy while evidence is thin (the household accepted this): an hour never heated in 90 days is never counted.
- If hive-app's poll interval changes, `POLL_MINUTES` must change with it (already noted for `heating_hour`).

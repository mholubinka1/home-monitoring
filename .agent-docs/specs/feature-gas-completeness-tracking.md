# Gas completeness tracking: knowing which days are really complete

## Problem Statement

About 13% of gas days from the period the Octopus API still serves (2024-10-09 onward) are unusable, and `daily_consumption_summary` cannot tell: 61 days are all-zero readings, 22 have no data at all, and 13 are partial days stored as a smaller positive total (for example 29 of 48 half-hours on 2025-03-12) that silently understate that day. Comparing the API today with our table showed they agree exactly, so the gaps are upstream (see the [investigation](../research/heating-model-prototype.md)); the problem on our side is that the summary stores only a day's total, so a partial day looks like a real one. Separately, the summary refreshes weekly, so the most recent days read zero for up to a week until the next run even though the gas has arrived (2026-10-05 reads 0.0 while its raw rows total 6.9 kWh). The heating model, the year-on-year view and any weekly or monthly comparison all need a reliable "this gas day is complete" signal.

## Solution

Record how many half-hours back each daily total, define a single "complete gas day" rule from it, backfill the counts for the days the API still serves, and refresh the summary daily so recent days settle within a day. Slice 0 of the gas/heating work: the model ([ADR-0030](../adr/0030-learned-heating-model-and-its-adoption-by-the-cost-forecast.md)) and the dashboard use it.

## User Stories

1. As the account holder, I want partial and missing gas days left out of efficiency numbers and comparisons, so that a day with a faulty meter link never looks like a saving.
2. As the account holder, I want each gas day to be marked complete or not from a single definition, so that every chart and the model agree.
3. As the account holder, I want yesterday's gas to show up in the summary within a day, so that recent days are not stuck at zero until the next weekly run.
4. As an operator, I want the number of readings behind each daily total recorded, so that a partial day can be recognised later even after the raw rows are pruned.
5. As a maintainer, I want the history we can still verify (from 2024-10-09) backfilled with counts, and the older days clearly marked as unverified, so that nothing is guessed.

## Implementation Decisions

- **New column.** `daily_consumption_summary` gains a nullable count of half-hour readings per day (both energies, one code path), added additively by Schema Sync ([ADR-0005](../adr/0005-additive-only-schema-sync.md)). NULL means "not known" (days older than what the API serves).
- **Counted from the raw rows.** The summary job counts the raw half-hours per London day when it totals them ([ADR-0010](../adr/0010-local-day-bucketing-python-vs-sql.md)).
- **Complete gas day.** A London day is complete when its total is above zero and its count equals the day's expected half-hours (46, 48 or 50 on daylight-saving days). A day with an unknown count is treated as complete unless its total is zero, and is reported as unverified. One shared definition is used by the model (octopus-app) and exposed by the column for dashboard SQL.
- **Backfill of counts.** The existing summary backfill, which already walks every API reading, also records the counts for the days the API still serves; days before 2024-10-09 stay NULL. It requests everything the API serves (its window widened from 730 days to three years; Octopus returns nothing earlier). Its one-time `job_run` gate is replaced by a data gate (decided during implementation, 2026-10-10): it runs at any startup where the summary's days with a known count reach back less than 6 months, so the first start after deploy fills the counts and a fresh, wiped or restored database fills itself. The longest consumers need more (heating model about 16 months, year-on-year about 24), which a full backfill provides; the 6-month trigger only decides when to fetch again.
- **Daily refresh.** The summary job runs daily after the daily raw consumption refetch (rather than weekly), keeping the trailing 14-day window, so recent days correct themselves as the gas arrives and a not-yet-complete day is simply marked incomplete. Refetch, summary and raw pruning run in that order in one daily 04:00 thread (pruning, still gated on that run's summary succeeding, becomes daily too); each step keeps its own `job_run` name.
- **Docs.** A glossary entry for Complete Gas Day; the research note records the investigation.

## Testing Decisions

- External behaviour at the existing consumption-summary seams (the summarization tests and the summary backfill tests) with the SQLite fixture, plus Schema Sync against the real MariaDB fixture for the new column.
- Good tests: a 48-half-hour day is complete; 36 of 48 is not; spring-forward (46) and autumn (50) days are complete; a zero total is incomplete even with 48 zero readings; an unknown count is unverified but usable; a day that fills in later becomes complete on the next daily refresh; the backfill records counts for API days and leaves older days NULL; the summary is scheduled daily.
- Prior art: the summary refresh and backfill tests and the DST-boundary tests from the local-day work.

## Out of Scope

- Fixing the upstream gaps, imputing or estimating missing days, and gas before 2024-10-09.
- Contacting Octopus, changing the raw consumption retention, and any dashboard or model change beyond using the rule.
- Deploying to the Pi (a separate, asked-first step).

## Further Notes

- Breakdown of the 726 days from 2024-10-09 to 2026-10-04 (630 good, 61 all-zero, 22 absent, 13 partial) and the evidence that the API reports the same zeros, are in the research note. Electricity has no gaps. The physical cause (possibly the gas meter's link to the communications hub) is unconfirmed.
- The API serves nothing before 2024-10-09, so the first 11 weeks we hold (from 2024-07-24) cannot be re-checked; they stay unverified.

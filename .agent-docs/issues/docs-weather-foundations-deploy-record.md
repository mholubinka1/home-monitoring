# Issues: docs-weather-foundations-deploy-record

## docs: record the weather-data-foundations deploy and correct the spec's reader claim — [#657](https://github.com/mholubinka1/home-monitoring/issues/657)

**Blocked by**: None

### What to build

Record the controlled deploy of #656 (2026-10-09) in the deploy note of
`.agent-docs/specs/feature-weather-data-foundations.md`, and correct that
note's step 4, which says the deleted hours have no reader: octopus-app's gas
cost forecast fits its weather regression on daily max temperatures read from
`weather_observation`. Tick #620's last criterion on the deploy evidence plus
the existing real-MariaDB Schema Sync test; no new container test, since the
migration has already run in production and will not run again.

### Acceptance criteria

- [x] Step 4 of the deploy note says the gas cost forecast reads this table,
      and that deleting the old rows leaves the hours before the hourly job's
      24-hour window without temperatures until the history backfill (#623),
      so the forecast may fall back to its flat-average method, and a partly
      covered day enters the fit with an understated max.
- [x] The deploy note ends with a dated deploy record: database
      `home_monitoring`; step 1 no duplicates, step 2 skipped; step 3 Schema
      Sync output; step 4 rows deleted and their range; the hourly job's first
      rows; step 5 deferred to #658.
- [x] #620's last criterion is ticked with a note citing the deploy and the
      existing container test, and the issues file banner no longer says it is
      unticked.

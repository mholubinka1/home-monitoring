# A learned heating model, computed by a daily octopus-app job, replaces the fixed-threshold regression and drives the gas panels and the cost forecast

Gas panels need an absolute answer to "when was heating not required, or overused", which needs a **heating threshold** (the outdoor temperature below which heating is needed) that is learned from the household's own data, not assumed. The cost forecast's existing gas regression assumes a fixed 15.5 C base on each day's maximum temperature over a 7-day window; on this household's data a plain fit puts the threshold near 12.6 C on the daily mean. We decided on one shared model, defined as follows.

**Model.** Over a rolling 12 months of complete London days (zero-gas rows are missing data; days with gas below about a third of the baseload are "away days" and are excluded), fit `gas = baseload + slope * max(0, threshold - effective_temperature)`, where `effective_temperature = (1 - w) * today's daily mean + w * yesterday's daily mean` (thermal memory) with `w` fitted in a bounded range. The threshold and `w` are found by trying a grid and keeping the best least-squares fit; the fit is plain (it describes the household's *typical* days, not its most efficient ones). The threshold's uncertainty comes from resampling.

**Verdicts.** Each day's allowed wobble scales with how much heating the day needed (noise grows with the cold). A day is "possible" waste beyond 1 standard deviation above expected and "clear" beyond 2; only the part beyond the margin is counted (so totals are a floor); days inside the threshold's uncertainty range count as needed, so "avoidable" gas is only counted on days clearly warmer than the threshold. Bars sum exactly to actual gas: baseload, expected heating, normal variation, possible, clear.

**Confidence.** The label is "estimated" until the thermostat data independently agrees: a heating day is at least 30 minutes of scheduled (non-boost) demand, and the model is "confirmed" when the thermostat-side threshold lies within about 1.5 C of the gas-based one with about 15 days of evidence on each side.

**Where and when.** A daily job in octopus-app (which owns the gas data and the cost forecast, and already reads weather tables from hive-app) refits weekly, recomputes every day's verdict with the latest model, and projects 7 days ahead; it writes three small tables (model, day verdict, forecast day) that Grafana and the cost forecast only read.

**Adoption.** The cost forecast reads the model straight away (the user chose this over adopting later), with a fallback to today's flat-average behaviour when no usable fit exists (fewer than about 150 usable days, fewer than 30 days on either side of the threshold, R-squared below 0.5, threshold at the edge of the search range, or a non-positive slope); the old regression is retired.

Surprising without context: this prefers several simple, conservative rules over statistical sophistication (a plain fit, standard-deviation multiples even though gas is right-skewed and flags more days than a normal curve predicts, floor-style amounts), and a rolling window means a persistent change in behaviour is absorbed into "typical", which is why the year-on-year view exists.

## Considered Options

- **Compute in Grafana SQL**: rejected; a fitted bend is awkward to express, every panel would carry its own copy, and the cost forecast could not use it.
- **Fixed 15.5 C base on the daily maximum**: rejected; the threshold is learned, and on this data the maximum fits no better than the mean while the blend with yesterday fits clearly better.
- **Fit to the efficient days (repeatedly trimming the highest days)**: rejected; one-sided trimming biases expected gas downward and overstates waste.
- **Percentile cut-offs instead of standard-deviation multiples**: recommended against, but the multiples were chosen for simplicity; the cut-offs are settings (for example raise "clear" to 2.5).
- **Adopt in the cost forecast only after the model has proven itself**: recommended, but adoption straight away was chosen; the fallback and a side-by-side check of old and new projections in the pull request are the safeguards.
- **A "conflicting" third label**: rejected for simplicity; a disagreement simply stays "estimated".

## Consequences

- The gas billing-period predictor's numbers change when this ships.
- Past verdicts and monthly totals can shift slightly after a weekly refit; the chart shows when the model was last fitted.
- On the prototype data the last 12 months differ sharply from the year before (baseload about 8.2 vs 5.6 kWh/day, threshold about 13.5 vs 12.0 C), so the model follows recent behaviour by design.
- Depends on [ADR-0029](0029-weather-history-backfilled-hourly-deduplicated-and-labelled-by-source.md) (weather history); related to [ADR-0016](0016-energy-column-on-cost-forecast.md), [ADR-0010](0010-local-day-bucketing-python-vs-sql.md) and [ADR-0022](0022-single-shared-home-monitoring-database.md).

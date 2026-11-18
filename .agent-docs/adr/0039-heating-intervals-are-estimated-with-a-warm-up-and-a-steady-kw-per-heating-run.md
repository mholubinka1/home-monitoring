---
status: accepted
---

# Heating intervals are estimated with a warm-up and a steady kW per heating run

A Heating Interval without measured gas was priced at one Effective Boiler kW for every minute on ([ADR-0033](0033-hourly-heating-cost-learns-a-hot-water-baseline-and-the-boilers-effective-kw.md)). The boiler does not draw steadily. When a Heating Run starts the radiator water is cold and the burner runs near full rate; once the water is hot the boiler turns itself down. On the household's 2026-10-21 to 2026-11-16 data the single kW was right in total but under-estimated the half-hour a run starts in by about a quarter and over-estimated the later half-hours by about a third, so the dashboard's tallest estimated bars always read low. We decided to estimate each Heating Interval from its minutes in the first 20 minutes of a Heating Run at a **Warm-up kW** and its later minutes at a **Steady kW**. Both are learned by least squares from the measured Heating Intervals of the last 28 days, and the single kW stays as the fallback when there are fewer than 10 of them or either figure is not positive. In a leave-one-day-out backtest this cut the mean miss per half-hour from 1.37 to 0.79 kWh (about 17 and 6 kW). Each row also keeps its **Heating Estimate**, frozen at its last value once measured gas arrives, so estimate and actual can be compared for the same half-hour.

This amends ADR-0033 for Heating Intervals only. The room forecast still prices its steps at the single Effective Boiler kW until it is moved onto the same model.

## Considered Options

- **Learning the warm-up length as well.** Every length from 14 to 24 minutes scored within a few percent of 20, and 42 runs are too few to fit a third number reliably. It is a named constant, revisited with more data.
- **A decaying power curve.** It may describe the physics better, but it needs a curve fit and gave no gain the data could show over two levels.
- **Scaling the warm-up by how long the heating was off before the run.** Runs after about 2 hours off came out about 20% over, but with this few runs the effect could not be told apart from noise. It is a later step, to be judged against the frozen Heating Estimates.
- **Recomputing the Heating Estimate of measured rows on every run.** The kW figures would then be learned partly from that row's own gas, so accuracy would look better than it is.

## Consequences

- `heating_interval` gains `early_on_minutes` (on-minutes in a run's first 20 minutes) and `estimated_heating_kwh`. Rows stored before this change are given `early_on_minutes` once from the kept thermostat polls; their Heating Estimate stays empty.

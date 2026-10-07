# Why leaving weeks out of the year-on-year line is not massaging the data

This note explains the week rules behind the weather-normalised year-on-year panel ([#643](https://github.com/mholubinka1/home-monitoring/issues/643)) in plain language. The numbers come from a throwaway analysis of the real gas and weather data on 2026-10-07; the method is repeatable from this note.

## The worry, stated fairly

The panel compares this winter's heating efficiency with last winter's, week by week, and it leaves some weeks out. Any time data is left out, there is a fair question: *are you removing the weeks that spoil the story?* If we were, the panel would be worthless, because it would only ever agree with whoever wrote the rules.

## What the three rules are, in one line each

A week appears only if it has **at least 5 complete gas days** (the meter link drops out), was **cold enough** (at least 1.5 degree-days a day, so the heating gas is bigger than ordinary hot-water wobble), and the heating **actually ran** (at least 0.5 kWh a day above baseload in both years). Each guards against a different kind of meaningless number: a week that is mostly gaps, a week with almost no heating to measure, and a week where the heating was off so the ratio divides by nearly nothing (one week came out "20 times worse", which is not a real change in the house).

## Why this is not cherry-picking

1. **The rules are about whether a number can be measured, not about what it says.** They look at how complete the week is and how much heating there was, never at whether this year looks better or worse. They apply identically to both years and to every week.
2. **The answer survives changing the rules.** We ran seven reasonable rule sets, from "no guards at all" to the strictest. The median ratio (this year divided by last year; above 1 means this year is less efficient) was between **1.02 and 1.24** every time. The size of the gap moves with the rules; the direction never did.
3. **The rules do not shrink the data to nothing.** They keep **14 of this winter's 28 weeks**, and those weeks carry **80% of the winter's degree-days and 72% of its gas**. They are not just the coldest or the mildest weeks, although they do lean colder (typical 4.8 degree-days a day against 3.2 for all weeks), on purpose, because a mild week has too little heating to measure.
4. **An independent check uses none of the rules.** Adding up the whole matched winter, with no weekly filtering, gives gas per degree-day about **17% worse** this year (about **9% to 18%** once baseload is removed). If the guarded weekly line and the unguarded winter total disagreed, we would stop trusting the guards. They agree in direction.
5. **Nothing is hidden on the panel.** Left-out weeks show as gaps, not as missing data, and the panel states how many weeks are shown and how many were left out and why, so a viewer can see exactly what the rules did.
6. **The rules are fixed and written down.** The three numbers are settings recorded in the design with the date and the data that informed them. Changing one needs a written reason, so they cannot quietly drift to suit a result.

## What we should admit

- **The numbers 1.5 and 0.5 were chosen after looking at this data, and the third rule was added after one bad week appeared.** They are reasonable and the reasoning is above, but they are not "decided in advance". The honest test is the next winter: if the same rules, unchanged, keep behaving sensibly on weeks nobody tuned them for, they have earned trust.
- **The weekly evidence on its own is weak.** The median ratio is **1.08**, and re-running the comparison on reshuffled copies of the weeks (a "bootstrap", see [bootstrapping](https://en.wikipedia.org/wiki/Bootstrapping_%28statistics%29)) gives a range of **0.91 to 1.27**, with about a **68% chance** that this winter really is less efficient. In plain words: **probably somewhat worse, not proven.** The guards cannot make weak evidence strong; they only stop noise being mistaken for a finding.
- **What would make it stronger** is more weeks (another winter) and fewer gas gaps, which is why the gas completeness work matters.

## What the panel will and will not say

It will say whether gas per degree of cold, with baseload removed, looks higher or lower than last year in the weeks it can measure, labelled "estimated". It will not say *why* (a higher setpoint, more boosting, a less efficient boiler, a changed baseload), and it will not claim certainty the data does not have.

## Reproducing the numbers

Gas: the daily gas summary for 2024-10-01 to 2026-09-30 (days with zero or partial readings excluded). Weather: Open-Meteo's daily mean temperature for the London day at the postcode area, blended 55% today and 45% yesterday, with degree-days measured against one common threshold (about 12.1 C) for both years. In this analysis each year's own baseload was taken from that year's warm days (about 6.6 and 8.6 kWh a day); the panel itself uses the moving baseload, which gives each week the baseload of its own time of year. Weeks are Monday to Sunday and compared 364 days apart, by per-day averages. Related: [prototype findings](heating-model-prototype.md), [ADR-0030](../adr/0030-learned-heating-model-and-its-adoption-by-the-cost-forecast.md), and for the ideas used, [confidence interval](https://en.wikipedia.org/wiki/Confidence_interval) and [heating degree day](https://en.wikipedia.org/wiki/Heating_degree_day).

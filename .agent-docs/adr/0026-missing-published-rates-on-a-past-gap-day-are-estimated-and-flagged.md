# Missing published rates on a past gap day are estimated and flagged, not fatal

Refines [ADR-0023](0023-gap-filled-days-get-an-estimated-variable-cost.md), whose "a day not fully covered by known rates still raises" rule made `cost_forecast_refresh` all-or-nothing: one upstream hole in a past day's rates (#575) failed the whole refresh, wrote no forecast, and left the previous row stale. A forecast that is approximately right and visibly flagged beats one that is exactly right or absent, so a past gap day that needs a variable cost and has missing or incomplete rates is now estimated, the forecast is still written, and the estimate is flagged.

- **Fallback unit rate.** The uncovered stretches of the day are priced at the time-weighted average unit rate (weight = segment duration) of that same day's published segments. Only what is genuinely missing is borrowed from the nearest earlier fully published day, searching back at most 7 days: the standing charge when no published rate covers local midday, and the unit rate as well when nothing is published for the day at all. With no such day to borrow from, it raises as before.
- **Which days.** Past gap days only. Today stays standing-charge-only and still raises when its midday rate is missing (the 2026-10-03 note in ADR-0023).
- **Cap.** At most 3 days per energy per refresh may have their rates estimated; a 4th raises and no forecast is written for that energy (electricity is persisted before gas is evaluated, as before, so an electricity row can already exist when gas raises). A short hole is a blip worth estimating through; a longer one signals a wrong product code or an upstream outage, where a confident estimate would mislead.
- **Flag.** `cost_forecast.rates_estimated` (boolean, default false) and `cost_forecast.estimated_days` (comma-separated ISO dates, nullable), added through Schema Sync. It means rate estimation only: days whose kWh is estimated but whose rates are fully published (ADR-0023) stay unflagged, or the flag would light on every gap. Each refresh appends a new `cost_forecast` row, so the flag clears when the newest row is unflagged; readers must take the latest row per energy and billing period.
- **Cadence.** `cost_forecast_refresh` runs hourly with the existing five-attempt backoff and no new alert, so a gap fills within an hour of its rates arriving. A persistent hole shows as a flag that does not clear.

## Considered options

- **Nearest published slot's rate:** rejected, Agile slot prices swing too much for one neighbour to be a fair guess.
- **`agile_forecast` predictions:** rejected, they cover only the future.
- **Fail loudly (status quo):** rejected, a stale forecast is worse than a flagged approximate one.

## Amendment: each gap day uses the agreement covering that day

Gap days (and the earlier-full-day search for a rate-less one) were first priced with the agreement current at `as_of`, so a tariff renewal inside the billing period priced earlier gap days at the new product, or failed the refresh when the new product had no rates for them. Each gap day is now priced with the agreement whose validity range covers its local midday, the same instant the standing charge is read at, so a renewal part-way through a day is decided by midday.

- **No covering agreement.** A gap day that no agreement covers raises, naming the day and energy, rather than being skipped; a forecast is never persisted with a silently unpriced day.
- **Fallback follows the gap day.** The earlier-full-day search uses the same agreement as the day it estimates, not the earlier day's own agreement. Consequence: if the product changed on the renewal day itself and that day has a rate hole, the search looks for the new product's rates on days before it existed and may raise "no fully published day".
- **Unchanged.** The remaining-days projection still prices at the agreement current at `as_of`; the elapsed-cost join already matched each instant to its own agreement.

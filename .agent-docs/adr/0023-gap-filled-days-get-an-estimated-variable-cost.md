# Gap-filled billing days get an estimated variable cost, not just the standing charge

Previously, a billing day with no complete consumption data (zero rows, or a strictly-past day still missing rows to the [day-completeness guard](0009-day-completeness-guard-standing-charge-fallback.md)) contributed only its standing charge to `actual_cost_to_date` — its variable (unit-rate) cost was silently omitted, understating the running total for as long as the gap persisted. `CostForecastRetriever._fill_zero_consumption_days` now estimates that day's kWh and prices it at the rate(s) actually published for that specific day, so the gap contributes a full (estimated, `is_gap_filled=True`) cost instead.

Two distinct gap shapes get different estimates:

- **Interior** — a later real day already exists in the billing period (data has resumed after the gap): estimate = average of the real day immediately before and immediately after the gap span, applied to every day in a multi-day span alike (not a linear interpolation across it).
- **Trailing** — nothing has settled after the gap yet: estimate = this period's own real-day average (`future_daily_kwh`, the same figure the remaining-days projection already uses).

If the billing period has no real day at all yet (day one, run before Octopus has returned anything), the estimate falls back to the average of the trailing week (`BOOTSTRAP_WINDOW_DAYS = 7`) immediately before the period started, read from `daily_consumption_summary`. If even that history doesn't exist (a brand-new account), the day falls back to the pre-existing standing-charge-only behaviour — there's nothing to estimate from.

Pricing itself uses a new `read_product_rates_for_local_day` query (every `product_rate` row overlapping the local day, overlap-weighted) rather than a single instant lookup, so Agile's half-hourly rate changes are priced correctly within a gap day instead of collapsing to one flat rate; a day not fully covered by known rates still raises, matching this module's existing "raise rather than guess" convention for money calculations. Gap-filled days remain excluded from `project_daily_average_consumption`'s input regardless of which estimate produced them — they're still not real observed usage.

The standing charge itself is a flat per-day fee, not prorated by rate coverage, so on the rare day a tariff renewal changes it mid-day, `_price_gap_day` uses whichever rate covers local midday as the day's single charge — matching the pre-existing midday-lookup convention this replaced, rather than `max()` across every rate touching the day (which would pick whichever happens to be larger, an arbitrary choice for a money calculation).

`product_rate` rows for one product/region are expected to be contiguous and non-overlapping, but a gap day's pricing doesn't trust that blindly: `_day_segments` partitions the day into non-overlapping segments up front, each resolved to a single winning rate (latest `valid_from` wins on a genuine overlap, the same "most-recently-started wins" convention `read_current_product_rate` already uses). Both the standing charge and the variable cost read from this one partition. An earlier version deduped the overlap only for the standing charge while summing every overlapping rate's own full window for the variable cost — silently double-billing the overlapping hours instead of raising or deduping consistently with the "raise rather than guess" convention. Handling it once, up front, for both figures closed that gap.

`as_of`'s own local date is excluded from the "real days" `_estimate_gap_day_kwh` interpolates or averages other gap days from, and from `_project_remaining_cost`'s `real_daily_totals`. `read_elapsed_billing_period_costs` exempts that date from the day-completeness guard precisely because it's still arriving — a real row, but a partial one — so treating it as a finished day's total silently understated both the interpolation and the remaining-period average on every refresh (the job never in practice runs at exact midnight: daily at 04:00 when this was written, hourly since ADR-0026). It's still counted at its own (partial) actual cost in `actual_cost_to_date`; only its use as an averaging/interpolation input is excluded.

`as_of`'s own local date is also never given an *estimated* variable cost when it's a gap itself (no consumption rows at all yet, as opposed to a real partial row) — it always falls back to standing-charge-only for that specific day, regardless of what its interior/trailing estimate would otherwise resolve to. `_remaining_billing_window`'s `remaining_hours` already spans from `as_of` through the end of the billing period specifically so the *rest* of today's not-yet-metered variable cost is counted exactly once via the remaining-cost projection; a full-day variable-cost estimate added to `daily_costs` for that same day would double-count it. This mirrors the pre-existing invariant that a same-day `daily_costs` row reflects only what's actually been metered (or, now, priced) so far, never a projection of the day's remaining hours.

Applies uniformly to electricity and gas.

## Note (2026-10-03): rate coverage is required only where it is used

Rate coverage is required only where it is used. A gap day with no estimated kWh (today) needs the rate at local midday for its standing charge, not the whole day; days needing a variable cost keep the full-day requirement and the original error.

Reason: Agile publishes to 23:00 UK local the next day, which in BST (22:00 UTC) is an hour short of local midnight (23:00 UTC). The strict whole-day check therefore made the forecast fail on every daily 04:00 UTC run during BST, even though today's standing-charge-only pricing reads a single midday rate (#574). The wider missing-rate policy and cadence question is tracked separately in #575.

## Note (2026-10-04): the "still raises" rule is superseded for past days

A past gap day whose rates are missing or incomplete no longer raises: it is estimated and flagged, up to 3 days per refresh, and the job now runs hourly. See [ADR-0026](0026-missing-published-rates-on-a-past-gap-day-are-estimated-and-flagged.md). Today's standing-charge-only rule above is unchanged.

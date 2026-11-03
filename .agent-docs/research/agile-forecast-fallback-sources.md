# GB Agile Rate Forecast — Fallback Data Source Research

> Trigger: agilepredict.com had a multi-hour total outage on 2026-08-04 (TCP connects,
> HTTP response never arrives, 30s timeout, reproduced from two networks). The
> `cost_forecast_refresh` job (04:00) exhausted its `@retry()` budget and produced no
> forecast that day. This document investigates fallback/alternative sources for the
> forward-looking Agile unit-rate forecast `_project_agile_variable_cost` needs.

## TL;DR

**No source matches agilepredict.com's combination of per-DNO-region granularity and
multi-week horizon except two other single-maintainer hobby ML projects in the same
risk category** (`agileforecast.co.uk`, `x2r.uk`). Official sources (Octopus's own API,
Elexon/NESO, Nord Pool) cap out at **~1 day ahead** — confirmed live against the
production Octopus API on 2026-08-04, and matches Nord Pool's and Elexon's own
documented day-ahead-only scope. There is no official, multi-day-ahead, per-region
Agile price forecast anywhere; every candidate that forecasts further than 1 day is an
unofficial ML hobby project with no SLA.

Best concrete recommendation: add **`x2r.uk`** as a second forecast source tried after
(or instead of, on failure of) `agilepredict.com` — same region-code format, comparable
horizon, a documented JSON API, and it is independent infrastructure from
agilepredict.com (so an agilepredict.com outage doesn't imply an x2r.uk outage). This
is a mitigation for *infrastructure* risk (one host/maintainer going down), not a
different *category* of risk (both are unaccountable hobby services) — see
"Recommendation" below for the honest ceiling if that residual risk matters more than
convenience.

## Context: how this codebase uses the forecast today

Read directly from source, not inferred:

- `AgilePredictClient.get_forecast` (`app/data/octopus/agile_predict.py`) calls
  `GET https://agilepredict.com/api/{region}/`, where `region` is `CostForecastSource.region_code`
  — a single-letter GB GSP/DNO group code (`A`–`P`) sourced from **Octopus's own** REST
  endpoint `industry/grid-supply-points` (`app/data/octopus/account.py:106`,
  `get_region_code`). It expects the JSON shape
  `[{"prices": [{"date_time": "...", "agile_pred": "..."}]}]` and takes only the first
  element of the outer list.
- `.agent-docs/adr/0002-agile-predict-forecast-dependency.md` records that agilepredict.com
  is the hosted public API for the open-source project `fboundy/agile_predict`
  (GitHub), and that this project deliberately chose to **consume** that public
  service rather than **build** an in-house ML pipeline, to avoid a CatBoost/
  LightGBM/scikit-learn dependency and a data-feed pipeline this project has no other
  need for. That trade-off is explicit and accepted — any fallback that requires
  building a forecasting model in-house re-opens a decision this project already made
  deliberately.
- `CostForecastRetriever._project_agile_variable_cost` and `tile_forecast_beyond`
  (`app/data/cost_forecast.py`) already assume the forecast **won't** cover a whole
  ~30-day billing period: whatever real/forecast readings come back, if they don't
  reach `billing_period_end`, the last 7 days of real data are tiled forward
  (day-of-week-aligned, same time-of-day rates repeated) to synthetically fill the
  rest. This matters for evaluating fallbacks — **a fallback with a shorter real
  horizon than 14 days does not break the pipeline**, it just means a larger fraction
  of the projection is tiled (flat-projected) rather than model-forecast. The existing
  design already treats "real forecast, then tile" as the normal shape, not an edge case.

## 1. Octopus's own official ceiling

Confirmed **live**, directly against the production API, on 2026-08-04:

```text
GET https://api.octopus.energy/v1/products/AGILE-24-10-01/electricity-tariffs/E-1R-AGILE-24-10-01-A/standard-unit-rates/?period_from=2026-08-04T00:00:00Z
```

returned rates only through `2026-08-05T21:30:00Z`–`22:00:00Z` — i.e. **today plus
tomorrow, not further**. `AGILE-24-10-01` is the current live Agile product per
`GET https://api.octopus.energy/v1/products/?is_variable=true` (`available_from
2024-10-01`, no `available_to`). This is the authoritative primary source: the API
itself, called live, not a doc page describing it.

Endpoint shape/pagination is documented at
[REST API — Endpoints](https://developer.oegb-kraken.energy/guides/rest/api-endpoints/), but that
page (and `https://docs.octopus.energy/rest/reference/`, `.../rest/guides/tariffs/`)
describe request/response shape only — **neither documents a publication-time SLA or
horizon**; the ~1-day ceiling is an observed behavior, not a documented contract.
Multiple secondary sources (energy-stats.uk, saveonenergybills.co.uk) consistently
describe "tomorrow's rates published daily after 4pm," which matches the live result
above, but no official Octopus page stating this was found. A search for holiday-period
exceptions (Christmas/New Year published further ahead) turned up nothing — treat this
as "not found," not "confirmed never happens."

**Conclusion: Octopus's own API ceiling is ~1–2 days ahead, full stop.** This is the
honest floor for any fallback that doesn't involve a third-party forecast model.

## 2. Community Agile-forecast alternatives to agilepredict.com

Three GB Agile forecast services were found, all structurally the same kind of project:
one hobbyist, an ML model trained on Elexon/NESO/weather data, ~14 GSP regions +
national aggregate, no SLA.

### 2a. `agilepredict.com` (current dependency)

Source: [`github.com/fboundy/agile_predict`](https://github.com/fboundy/agile_predict)
(fetched directly via `gh api`, not a secondhand description).

- README, verbatim: "Forecasts Octopus Agile electricity import and export prices up
  to 14 days ahead using an ensemble machine learning model. Covers all Agile regions
  (A–P) plus a national aggregate." Three-model ensemble (CatBoost, LightGBM,
  ExtraTrees), inputs from Elexon BMRS, NESO, ENTSO-E, Open-Meteo, Octopus (actuals),
  Nord Pool, Yahoo Finance (TTF gas futures). Hosted on Fly.io (`app name: prices`).
- Repo activity: 50 stars, 3 open issues, `pushed_at: 2026-08-04T20:40:01Z` — actively
  maintained, single maintainer.
- **Directly relevant to today's outage**: the repo's own commit history from
  **2026-08-04** (the same day as the reported outage) documents the maintainer
  live-firefighting a cascading production failure — commit messages describe
  "Gunicorn's 'Perhaps out of memory?'", workers wedging, "SIGTERM... blocked in
  sendall... arbiter escalation to SIGKILL", and "one machine wedged and the survivor
  followed within two minutes, producing a total outage." A GitHub issue titled
  **"Loading very slow and not reliably"** was opened the same day at
  `2026-08-04T09:47:39Z`, still open. This is first-party confirmation that today's
  outage is not a fluke — it's a known, currently-unresolved reliability problem on
  the maintainer's own infrastructure, documented by the maintainer themselves.

### 2b. `agileforecast.co.uk`

- Site's own text (fetched directly): forecasts "21 days ahead" (longer horizon than
  agilepredict.com), "all 14 Octopus GSP regions plus a national GB average," inputs
  from "BMRS GB grid forecasts, Open-Meteo weather ensemble data, ENTSO-E cross-border
  flow schedules, and TTF gas futures," produces "low–central–high band" predictions,
  and states "Confirmed Octopus prices override the model for published slots." Has an
  `/api-docs` link and an `/accuracy` page.
- **No maintainer or GitHub repo identified anywhere on the site** — searched
  specifically, found nothing. This is materially different from agilepredict.com:
  there's no way to check commit history, issue reports, or hosting setup, so there's
  no way to independently gauge its maintenance health or reliability the way the
  agile_predict repo above allowed. Full `/api-docs` schema could not be retrieved (the
  page appears to require client-side rendering past the nav shell) — an implementer
  would need to fetch it directly in-browser or find another way in before relying on
  it.

### 2c. `x2r.uk`

- Site's own text (`x2r.uk/agile/index.php`, fetched directly): ~14-day forecast
  (selectable 2/5/7/10/14-day views), same 14 GSP regions (A–P), explicit self-description
  as **"Not affiliated with Octopus or any organisation, just an Agile customer"** —
  a personal hobby project, not a company. Data sources: NESO Open Data, Elexon BMRS,
  Octopus Energy actuals, Nord Pool day-ahead prices, Open-Meteo.
- **Has a documented JSON API**, fetched directly at
  [`https://api.x2r.uk/?agile_doc`](https://api.x2r.uk/?agile_doc):
  - `GET https://api.x2r.uk/agile/{region}` — `region` is a "Single letter DNO region
    code... Case-sensitive," the same A–P GSP letters this codebase already derives
    from Octopus's own `industry/grid-supply-points` endpoint. No adapter needed for
    region-code format.
  - Response: `{"forecast_at": "...", "region": "...", "region_name": "...", "prices":
    {"forecast": [...], "day_ahead": [...], "actual": [...]}}`, each price entry
    `{"date": "<ISO8601, Europe/London>", "price": <p/kWh, VAT-inclusive>}`.
  - Explicitly states: "Forecast data carries uncertainty beyond 48 hours,"
    "non-commercial use only," "no accuracy guarantees."
  - **Response shape differs from `AgilePredictClient`'s** — not a drop-in URL swap.
    Field names differ (`date`/`price` vs `date_time`/`agile_pred`), and the payload
    is a single object with three named price arrays (`forecast`/`day_ahead`/`actual`)
    rather agilepredict's list-of-regions-with-one-`prices`-array. A fallback client
    would need its own Pydantic models and its own mapping into
    `AgileForecastReading`, mirroring `AgilePredictClient`'s existing pattern rather
    than reusing it.
- **agile_predict's own README references x2r.uk by name** ("Forecast comparison
  overlays from AgileForecast and X2R can be toggled on") as one of two services it
  cross-checks itself against on its `/v2/<region>/` UI — informal third-party
  validation that these are considered peer/comparable services within the same
  hobbyist community, not that one is more authoritative than the other.

**None of the three is more "official" than the others** — they're the same category
of single/small-team hobby project, just different individuals and different hosting.
Adding a second one as a failover reduces *infrastructure* single-point-of-failure risk
(different host, different maintainer, so an agilepredict.com Fly.io cascade doesn't
take out x2r.uk) but does not reduce *category* risk (no SLA, no accountability, could
each independently have their own bad day).

## 3. Elexon BMRS / Insights Solution

Elexon's own [API Developer Portal](https://developer.data.elexon.co.uk/) and
[BMRS API docs](https://bmrs.elexon.co.uk/api-documentation/guidance) expose **physical
forecasts** (demand, wind/solar generation), not price forecasts:

- `forecast/demand/day-ahead` (dataset `NDF`/`TSDF`) — national demand forecast,
  **day-ahead only** per its own naming and the "Day and day-ahead" framing on
  [elexon.co.uk's NDF glossary entry](https://www.elexon.co.uk/bsc/glossary/national-demand-forecast/).
- `forecast/generation/wind-and-solar/day-ahead` (dataset `DGWS`/`B1440`) — also
  day-ahead only, per its own endpoint name.

Neither is a price forecast at all — no Elexon dataset publishes a forward £/MWh or
imbalance-price forecast beyond day-ahead. Critically, **these are exactly the kind of
inputs `agile_predict` itself already ingests** to train its ML ensemble (its README
lists Elexon BMRS as a data source for "UK nuclear availability, demand"). Using Elexon
data directly as an Agile-rate proxy would mean re-deriving the same kind of model
`agile_predict` already runs — the in-house ML pipeline ADR 0002 explicitly decided
against building.

**NESO's own Data Portal** (National Energy System Operator, the body whose
forecasts Elexon republishes) does separately publish longer-horizon **demand**
forecasts — confirmed via NESO's own portal page titles:
[2-14 Days Ahead Demand Forecast](https://www.neso.energy/data-portal/2-14-days-ahead-national-demand-forecast)
and even [Long term (2-52 weeks ahead) National Demand Forecast](https://www.neso.energy/data-portal/long-term-2-52-weeks-ahead-national-demand-forecast).
These extend much further than any price-forecast candidate above — but they are
**national aggregate demand in MW**, not per-DNO-region price in p/kWh, and turning
demand-MW into Agile-rate-£/kWh-per-region is precisely the ML modeling problem
`agile_predict`/`agileforecast.co.uk`/`x2r.uk` already solve. Not usable as a direct
substitute without building that pipeline.

## 4. Nord Pool / N2EX day-ahead auction

Confirmed via Nord Pool's own support article,
["About the N2EX Day Ahead Auction"](https://support.nordpoolgroup.com/support/solutions/articles/8000088463-about-the-n2ex-day-ahead-auction):
gate closure 09:50 GMT, results published ~10:00 GMT, for **next-day (d+1) physical
delivery only**. No forward curve or multi-day-ahead GB auction product is published —
day-ahead auctions are, definitionally, day-ahead. This is strictly narrower than even
Octopus's own ~1-2 day ceiling (§1), and it's a wholesale price, not a retail Agile
unit rate — Octopus's margin/formula converting wholesale → Agile retail price isn't
published, so this would need the same kind of conversion the ML projects already do.
Not a useful fallback in any dimension beyond what's already available more directly
from Octopus's own API.

## 5. Is there a source matching agilepredict.com's horizon + regional granularity?

**No official one.** The only sources with (a) per-DNO/GSP-region granularity and (b)
a horizon meaningfully beyond ~1-2 days are `agileforecast.co.uk` and `x2r.uk` — the
same category of unaccountable single-maintainer hobby project as agilepredict.com
itself, just independently hosted. Every source with genuine institutional backing
(Octopus's own API, Elexon, NESO, Nord Pool) caps at ~1 day ahead, confirmed against
each one's own live behavior or documentation, not inferred. If "genuinely more
reliable, not just a different hobby project" is the bar, **the honest ceiling for a
fallback is Octopus's own next-day published rate, then flat/tiled-projected** for the
rest of the billing period — which the codebase's existing `tile_forecast_beyond`
mechanism already does mechanically for whatever portion of the period a forecast
doesn't cover, so this isn't a new code path, just a shorter "real" input to it.

## Recommendation

1. **Cheap, concrete mitigation for today's failure mode**: add `x2r.uk`
   (`https://api.x2r.uk/agile/{region}`) as a second forecast client, tried on
   `AgilePredictClient` failure (or queried in parallel and preferred by
   freshness/success). Same region-code format as this codebase already derives from
   Octopus, comparable ~14-day horizon, documented JSON API, and — most importantly —
   independent hosting from agilepredict.com's Fly.io deployment, so it isn't exposed
   to the same cascade documented in `fboundy/agile_predict`'s own commit history
   today. This requires a new Pydantic response model and its own
   `AgileForecastReading` mapping (schema differs from `AgilePredictClient`'s, see
   §2c) — not a URL swap.
2. **If the real goal is removing exposure to "any hobby project might vanish or have
   a bad day" entirely** rather than just adding redundancy: don't chase a second ML
   forecast. Fall back to Octopus's own `standard-unit-rates` endpoint (already have
   `OctopusTransport`-style REST clients in this codebase) for the ~1-2 days it
   actually publishes, and let `tile_forecast_beyond` carry the remainder of the
   billing period exactly as it already does today for the tail beyond whatever
   forecast is available. This trades forecast accuracy for zero new third-party
   dependency — worth an explicit product decision (accuracy vs. dependency risk), not
   an implementation detail, before whoever picks this up starts coding.
3. Either way, this is additive to ADR 0002, not a reversal of it: neither
   recommendation proposes building an in-house ML pipeline (Elexon/NESO route, §3),
   which ADR 0002 already considered and declined.

**Open item for implementation**: `agileforecast.co.uk`'s `/api-docs` page did not
render its full schema via plain fetch (likely client-side rendered) — if it's
considered as a third option, that page needs a real browser fetch or another way in
before its exact request/response shape can be documented, unlike x2r.uk's, which is
fully captured above.

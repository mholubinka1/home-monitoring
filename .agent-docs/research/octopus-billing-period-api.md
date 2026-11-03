# Octopus Energy Billing Period API — Research

> **Addendum**: `currentBillingPeriodStartDate`/`currentBillingPeriodEndDate` (see the
> smoke test below) report Octopus's account statement/ledger window, not the tariff
> charge window Octopus actually bills against — confirmed one day later on both ends
> across 6 real bills. `BillingPeriod.from_billing_options` (`app/data/octopus/model.py`)
> corrects for this; see `.agent-docs/specs/bugfix-billing-period-offset.md`.

## TL;DR

The REST v1 API this codebase already uses (`OctopusTransport`, `auth=(api_key, "")`)
does **not** expose billing period, statement, or invoice data anywhere. That data
only exists in Octopus's Kraken GraphQL API, at `https://api.octopus.energy/v1/graphql/`,
under `account(accountNumber).billingOptions` (fields `currentBillingPeriodStartDate`,
`currentBillingPeriodEndDate`, `nextBillingDate`) and `account(accountNumber).bills`
(fields `fromDate`, `toDate`, `issuedDate`, `billType`).

The good news: live introspection of the production GraphQL schema shows the
`obtainKrakenToken` mutation's input type (`ObtainJSONWebTokenInput`) currently
accepts a standalone `APIKey` field — "API key of the account user. Use standalone,
don't provide a second input field." That is the same API key already stored in this
app's config and passed as the REST Basic Auth username. **No stored customer
email/password is required by the schema as it exists today.** This contradicts
several secondary sources (and Octopus's own example repo) that show email/password
login, so this must be treated as a finding that needs a live smoke-test spike before
committing to it (see the caveat in Question 2 below) — but if it holds, the
"integration" is a second, lightweight HTTP client (JSON-over-POST GraphQL calls,
reusing the existing API key), not a new credential-storage or interactive-login
concern.

Real billing-period fetching is feasible without a bigger auth model. It requires a
new (small) GraphQL client, not a new credential-storage story.

## 1. REST v1 billing/invoice data

The `/v1/accounts/{account_number}/` endpoint's documented response schema contains
no billing-period, statement, or invoice fields at all. Per
[Endpoints — REST API docs](https://developer.oegb-kraken.energy/guides/rest/api-endpoints/)
(the current canonical location; the older `developer.octopus.energy/rest/reference/`
URL now serves the same underlying docs), the response shape is:

- Top level: `number`, `properties`
- `properties[]`: `id`, `moved_in_at`, `moved_out_at`, `address_line_1..3`, `town`,
  `county`, `postcode`, `electricity_meter_points`, `gas_meter_points`
- `electricity_meter_points[]`: `mpan`, `profile_class`, `consumption_standard`,
  `meters`, `agreements`, `is_export`
- `gas_meter_points[]`: `mprn`, `consumption_standard`, `meters`, `agreements`
- `agreements[]` (on either meter-point type): `tariff_code`, `valid_from`, `valid_to`

None of these are billing-cycle or invoice fields — `agreements[].valid_from`/`valid_to`
mark when a *tariff* applies (e.g. a price change), not when a bill was issued or when
a monthly billing period starts/ends. There is no `next_billing_date`, `statement`,
`invoice`, or `bill` field anywhere in this schema. This means the REST v1 account
endpoint cannot answer "what's my current billing period" — only "what tariff code
and rate history applies to each meter point."

## 2. GraphQL "Kraken" API

**Endpoint.** Confirmed by live introspection (POST body
`{"query":"{__schema{queryType{name}}}"}` against
`https://api.octopus.energy/v1/graphql/` on 2026-07-21, HTTP 200 with a valid schema
response) — the commonly cited URL is correct, no redirect, no `/graphql` vs
`/graphql/` trailing-slash issue observed.

**(a) Billing/invoice/statement data exists, nested under `account`.** Introspecting
`AccountType` (`__type(name:"AccountType"){fields{name description}}`) returns, among
others:

- `bills` — "Fetch issued bills (invoices/statements) for the account." →
  `BillConnectionTypeConnection`
- `bill` — "Fetch a specific issued bill (invoice/statement) for the account." →
  `BillInterface`
- `annualStatements` — "Fetch annual statements for the account." →
  `AnnualStatementConnectionTypeConnection`
- `billingOptions` — "Information about the account's billing cycle." →
  `BillingOptionsType`

Introspecting `BillingOptionsType` (`__type(name:"BillingOptionsType"){fields{name
description}}`) returns exactly the billing-period fields this feature needs:

| field | type | description (verbatim) |
| --- | --- | --- |
| `periodStartDay` | `Int` | "The day of the month on which the account's billing period should start." |
| `periodLength` | `AccountBillingOptionsPeriodLength` | (enum, e.g. monthly/quarterly) |
| `periodLengthMultiplier` | `Int` | (unlabeled in schema; multiplies `periodLength`) |
| `isFixed` | `Boolean` | "If true, this account is billed on specific day of a regular cycle. If false, the billing schedule is flexible, depending on when meter readings are submitted." |
| `currentBillingPeriodStartDate` | `Date` | "The date on which the current billing cycle started." |
| `currentBillingPeriodEndDate` | `Date` | "The date on which the current billing cycle will end. Null if the account is on flexible billing." |
| `nextBillingDate` | `Date` | "The next date on which this account will next be billed. This is the same as the start date for their next bill cycle. Null if the account is on flexible billing." |

Introspecting `BillInterface` returns: `id`, `billType` (`BillTypeEnum`), `fromDate`
(`Date` — "The date of the bill is covered from"), `toDate` (`Date` — "The date of the
bill is covered to"), `issuedDate` (`Date` — "The date the bill was sent to the
customer"), `attachments` (`BillingAttachmentConnectionTypeConnection`),
`reversalsAfterClose` (`StatementReversalsAfterClose`).

Source for all of the above: live GraphQL introspection queries run directly against
`https://api.octopus.energy/v1/graphql/` on 2026-07-21 (`__type(name:"AccountType")`,
`__type(name:"BillingOptionsType")`, `__type(name:"BillInterface")`). This is the
schema itself, not documentation prose, so it is the most authoritative source
available — but note account-level fields can be gated by permissions per-token, so
this confirms the fields *exist in the schema*, not that every token/scope can read
every field (not independently verified here since no live API key was available to
this research task).

**(b) Authentication — this is the part that needs a caveat.** Live introspection of
`ObtainJSONWebTokenInput` (`__type(name:"ObtainJSONWebTokenInput"){inputFields{name
description}}`, same endpoint, same date) returns exactly five fields, each described
as mutually exclusive ("Use standalone, don't provide a second input field"):

- `APIKey` (`String`) — "API key of the account user. Use standalone, don't provide a
  second input field."
- `organizationSecretKey` (`String`) — "Live secret key of an third-party organization.
  Use standalone, don't provide a second input field."
- `preSignedKey` (`String`) — "Short-lived, temporary key (that's pre-signed). Use
  standalone, don't provide a second input field."
- `refreshToken` (`String`) — "The refresh token that can be used to extend the expiry
  claim of a Kraken token. Use standalone, don't provide a second input field."
- `captchaResponse` (`String`) — "The response from the CAPTCHA challenge. Use with
  'email' and 'password' fields."

The `obtainKrakenToken` mutation itself (introspected from `Mutation.fields`) takes a
single non-null `input: ObtainJSONWebTokenInput!` argument and returns
`ObtainKrakenJSONWebToken`, whose fields are `token` (non-null `String` — "Can be used
in the `Authorization` header for subsequent calls"), `payload` (JWT claims),
`refreshToken` (nullable `String`), `refreshExpiresIn` (nullable `Int`, Unix
timestamp). Per the `obtainLongLivedRefreshToken` mutation's own description: "Account
users can only generate short-lived refresh tokens, obtainable from the
'refreshToken' field in the 'obtainKrakenToken' mutation" — long-lived refresh tokens
are explicitly restricted to "authorized third-party organizations only."

**The caveat:** `captchaResponse`'s description references `'email'` and `'password'`
fields that do **not** currently appear in the live `inputFields` list, and Octopus's
own official example repo,
[octoenergy/oejp-api-example](https://github.com/octoenergy/oejp-api-example/blob/main/octopus.py)
(raw file fetched directly), authenticates with exactly that removed-looking shape:

```graphql
mutation obtainKrakenToken($input: ObtainJSONWebTokenInput!) {
  obtainKrakenToken(input: $input) {
    refreshToken
    refreshExpiresIn
    payload
    token
  }
}
```

```python
"variables": {"input": {"email": OCTOPUS_EMAIL, "password": OCTOPUS_PASSWORD}}
```

Other current docs pages describe the same thing: a WebFetch summary of
[GraphQL API Guides — API basics](https://developer.oegb-kraken.energy/guides/graphql/api-basics/)
and search-indexed content from
[Mutations — API docs](https://docs.octopus.energy/graphql/reference/mutations/) both
describe `obtainKrakenToken` as accepting "email/password, an API key, or a pre-signed
key" as three alternative auth modes (**flagged as secondary** — these came back as
AI-summarized fetches of the docs site rather than verbatim quotes, since the docs site
appears to be a client-rendered SPA that a plain fetch could not render past the
navigation shell).

Reading these together, the most defensible conclusion is: **email+password almost
certainly still works as an auth mode for `obtainKrakenToken`** (it's in Octopus's own
example code and is referenced by the `captchaResponse` field's description), but the
account-user API key *also* works and is explicitly documented as a standalone,
first-class input field in the live production schema today. This is the key
distinction the research task asked for: **the existing REST API key is sufficient by
itself** — `obtainKrakenToken(input: {APIKey: "<existing_api_key>"})` — to mint a
Kraken JWT for a headless background service. Storing a customer email+password is
not required by the schema; it is simply one of several supported alternatives (also
used for the interactive/consumer app login case, which is not this project's
situation).

Given that live-introspection and stored-credential evidence slightly disagree with
Octopus's own example repo about which fields are current, **this should be verified
with one real smoke-test call** (`obtainKrakenToken` with a real `APIKey` against the
account) before it's load-bearing in a design — the introspection is authoritative for
schema *shape*, but not for runtime authorization behavior (e.g. whether an
account-user API key is actually accepted at the resolver level, versus merely present
as an accepted input shape). No such live call was made in this research task since it
would have required this project's real API key and account number.

## 3. Cheaper REST v1 fallback/proxy

No, the REST v1 account endpoint has nothing resembling a "next billing date." As
established in Question 1, the full response schema for
`/v1/accounts/{account_number}/` is `number`, `properties[]` (address/meter-point
metadata), and per-meter-point `agreements[]` with only `tariff_code`, `valid_from`,
`valid_to`. There is no `periodStartDay`, `nextBillingDate`, or any field describing a
billing/invoice cycle. Source:
[Endpoints — REST API docs](https://developer.oegb-kraken.energy/guides/rest/api-endpoints/).

`agreements[].valid_from`/`valid_to` mark tariff validity windows — i.e., "this tariff
code applied to this meter point between these two dates" — which change only when a
customer's rate changes (e.g. a fixed-term deal ending, or a variable-rate price
revision). They are not framed anywhere in the docs as billing-cycle boundaries, and
per the GraphQL schema's own `BillingOptionsType.isFixed` description, an account's
billing schedule can be "flexible, depending on when meter readings are submitted" —
i.e., independent of tariff validity entirely. Tariff agreement windows are commonly
multi-month or open-ended (`valid_to: null` for the current agreement), not aligned to
a rolling monthly billing date, so treating `valid_from`/`valid_to` as a stand-in for
billing-period start/end would be **not just imprecise but structurally the wrong
concept** — it answers "which tariff price applies" not "when do I get billed and for
what date range." There is no documented or implied billing-cycle framing in the REST
v1 accounts response.

## Smoke test (live, 2026-07-21)

Ran the two calls the recommendation below describes, against this project's real
account and API key:

1. `obtainKrakenToken(input: {APIKey: "<real key>"})` → **succeeded**, returned a valid
   JWT. Decoded payload: `"gty":"API-KEY"`, `"sub":"kraken|account-user:<redacted>"`,
   `"email":"<redacted>"` — confirms the account-user API key is accepted at the
   resolver level, not just present in the schema shape. The auth-path caveat above
   is resolved: **the existing REST API key is sufficient**, no email/password
   needed.
2. `account(accountNumber: "<redacted>") { billingOptions { ... } }` with
   `Authorization: JWT <token>` → returned:

   ```json
   {"periodStartDay":null,"periodLength":null,"periodLengthMultiplier":null,
    "isFixed":false,"currentBillingPeriodStartDate":"2026-07-06",
    "currentBillingPeriodEndDate":null,"nextBillingDate":null}
   ```

**This account is on flexible billing** (`isFixed: false`), exactly the case the
schema's own field descriptions warned about in Question 2. `currentBillingPeriodStartDate`
is populated and usable, but `currentBillingPeriodEndDate` and `nextBillingDate` are both
`null` — there is no fixed end date to project a forecast toward. This directly affects
the "Total expected cost for this billing period" requirement: a flexible-billing account
has no schema-provided answer for "when does this period end," since real-world billing
for flexible accounts is driven by whenever a meter reading is next submitted/processed,
which Octopus's own schema does not expose a prediction for.

## Recommendation

Do the small GraphQL integration, not a config-file approximation. The REST v1
fallback data (agreement `valid_from`/`valid_to`) is the wrong concept for billing
periods, not just an imprecise version of it, so it cannot substitute for real billing
period data — it would silently produce wrong "cost so far this period" boundaries
whenever a tariff spans more or less than a billing cycle (the common case).

Concretely, the recommended integration is:

1. Add a small Kraken GraphQL client (a sibling to `OctopusTransport`, not a
   replacement) that POSTs JSON queries to `https://api.octopus.energy/v1/graphql/`.
2. Reuse the existing stored API key (`OctopusAPISettings.api_key`) — no new secret
   needs to be introduced or stored. Call `obtainKrakenToken(input: {APIKey:
   "<existing_key>"})` to mint a short-lived JWT, use it as `Authorization: JWT
   <token>` for the billing-period query, and discard it. Because the poller already
   runs on a schedule and the API key itself doesn't expire, there's no need to
   persist or rotate a refresh token — just re-authenticate with the API key each run.
3. Query `account(accountNumber: "...") { billingOptions { currentBillingPeriodStartDate
   currentBillingPeriodEndDate nextBillingDate isFixed } }` for the period boundaries
   the Cost/Cost Forecast feature needs, falling back to `bills`/`bill`
   (`fromDate`/`toDate`/`issuedDate`) if a fully-closed historical period is needed
   instead of "current, possibly still open" period.
4. Before committing to this in an ADR, run one real smoke test: call
   `obtainKrakenToken` with the project's actual API key against the actual account
   number, to confirm the account-user `APIKey` input field is genuinely accepted at
   the resolver level (introspection confirms it exists in the schema; it does not
   confirm authorization behavior). If it is rejected for this account for any reason,
   the fallback is Octopus's own documented email/password flow — which would then
   require deciding whether to store customer credentials, a materially bigger and
   more sensitive engineering/security decision than anything else in this document,
   and worth its own design conversation before adopting.

This is not the "bigger integration requiring stored username/password" scenario the
research question was worried about — it is closer in shape to the existing
`OctopusTransport` (a stateless HTTP client using the one credential already
configured), just against a different Octopus endpoint and wire format (GraphQL POST
instead of REST GET/Basic-Auth).

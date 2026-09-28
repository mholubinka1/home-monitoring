# hive-app: fix fresh Hive login crash

## Problem Statement

hive-app's very first login to a Hive account — the case with no persisted
`hive_auth_state.json` yet — crashes with an unhandled `KeyError:
'refreshToken'` instead of completing a real login. Discovered live during
hive-app's first-ever deployment to the Pi (issue #533): the account's login
attempt reached `apyhiveapi`'s internal token-refresh logic with no tokens to
refresh, because no actual Cognito login had ever taken place.

The account owner has no way to know hive-app is broken beyond watching it
crash-loop — there is no clean failure signal, no `job_run` record explaining
why, and (for the one genuinely unattended-service-breaking case, an account
needing a live SMS code) no ntfy alert, even though that exact alerting path
already exists in the codebase for a related scenario.

## Solution

`HiveApiSource`'s fresh-login path calls `apyhiveapi`'s real login entry
point (`Hive.login()`) instead of `Hive.startSession()` — the method it
currently calls, which is only meant for resuming a session with already-known
tokens, not establishing one. On success, devices are populated with a
follow-up `startSession()` call passing an explicitly empty config (`{}`),
which skips re-processing the tokens `login()` just obtained (avoiding
clobbering them) and falls through to that method's own device-population
logic. On the one interactive case a headless service structurally cannot complete
(the account needs a live SMS 2FA code and has no previously-registered
device), the existing `HiveReauthRequired`/ntfy-alert path fires cleanly,
using a message the code already has defined for exactly this case.

## User Stories

1. As the account owner, when hive-app logs in to a Hive account for the
   first time and the account needs no interactive step, I want it to
   complete that login and start polling normally, so that a fresh
   deployment works without manual intervention.
2. As the account owner, when hive-app's first login needs a live SMS 2FA
   code (an account with 2FA enabled and no previously-registered device), I
   want to be notified via the existing ntfy re-auth alert, so that I know
   hive-app needs my attention rather than discovering it's silently broken.
3. As the operator, I want this failure (and success) visible in `job_run`
   like every other job in this repo, not a special case — this is already
   true structurally (the crash currently still gets recorded as a failed
   job run), the fix doesn't change that behaviour, it changes what error
   gets recorded.

## Implementation Decisions

- **Root cause**: `HiveApiSource._establish_session`'s fresh-login branch
  (`state is None`) calls `HiveApiSource._start_session(hive,
  session_config=None, ...)`, which calls `hive.startSession()` with no
  arguments. In the real `apyhiveapi==1.0.9` library, `startSession(config)`
  defaults a `None` config to `{}` and then only processes
  tokens/username/password/device_data (and raises `HiveUnknownConfiguration`
  for a genuinely-unknown config) inside an `if config != {}:` block — so a
  truly empty config silently skips all of that and falls straight through to
  `getDevices()`, which fails internally trying to refresh tokens that were
  never obtained. `HiveSession.login()` (so `hive.login()`) is the library's
  actual dedicated entry point for a fresh interactive login (real Cognito
  `USER_SRP_AUTH` via `self.auth.login()`) — `HiveApiSource` never calls it.
  A related, previously-unverified assumption in `_resume_config`'s docstring
  comment (claiming `startSession()` performs "a full interactive login" when
  no `tokens` key is present) is also incorrect against the real library
  source and should be corrected as part of this fix, not left standing.

- **`hive.login()`'s three outcomes** (read directly from `apyhiveapi`'s
  installed source, `HiveSession.login`'s own documented "Business Rules"):
  1. Success — `result["AuthenticationResult"]` present. This also covers the
     case where an internal device-registration challenge was transparently
     handled by `login()` itself (`_handleDeviceLoginChallenge`, when the
     device *is* already registered) — from the caller's side, this looks
     identical to a plain success. `login()` already calls
     `self.updateTokens(result)` internally in this case, populating
     `hive.tokens.tokenData`.
  2. `ChallengeName == SMS_MFA_CHALLENGE`, no `AuthenticationResult` — the
     account needs a live SMS code and (implicitly, since this is the
     fresh-login path with no persisted device data) has no remembered
     device to fall back to. `login()` does not raise for this case; it
     returns the raw challenge dict.
  3. Any other exception `login()` itself can raise (`HiveInvalidUsername`,
     `HiveInvalidPassword`, `HiveApiError`, `HiveInvalidDeviceAuthentication`,
     `HiveUnknownConfiguration` for an unrecognised challenge) — none of
     these have defined handling in this codebase today and none gain any
     here; they surface as unhandled errors, consistent with this method's
     existing "raise rather than guess" convention elsewhere for conditions
     with no defined recovery.

- **Fixed call sequence**, extracted into its own `HiveApiSource._fresh_login`
  method (called from `_establish_session`'s fresh-login branch):
  1. Call `hive.login()`.
  2. If `AuthenticationResult` is present: call `_start_session(hive,
     session_config={}, ...)` — **not** a tokens-bearing config as originally
     planned (see "Implementation deviation" below) — so `getDevices()` +
     `createDevices()` run and populate `hive.deviceList`/`hive.data`,
     required before `_fetch_heating_status`'s device-list read works. This
     second call reuses the *existing* `ApyHiveReauthRequired` →
     `HiveReauthRequired` translation already in `_start_session`, unchanged.
  3. If the result has `ChallengeName == SMS_MFA_CHALLENGE` and no
     `AuthenticationResult`: raise this repo's own `HiveReauthRequired`,
     using the existing `_LOGIN_REQUIRES_SMS_MESSAGE` constant (already
     defined, currently unused on any reachable path) — the same
     `notify_reauth_required()`/ntfy-alert wiring this exception type already
     triggers for the resume-path's "device forgotten" case fires
     identically here, with a message distinguishing "first-ever login needs
     a human" from "something that used to work broke."
  4. Anything else `login()` *raises* propagates on its own. A *returned*
     dict matching neither outcome above (added during code review, not
     originally planned) raises a `RuntimeError` explicitly, rather than
     silently returning and letting the caller persist a `HiveAuthState`
     with an empty `refresh_token` as though login had succeeded.

- **Implementation deviation from the original plan (found during code
  review, not this spec's original design)**: this spec originally called
  for a shared `_tokens_config(refresh_token: str) -> dict` builder, used by
  both `_resume_config` (a *persisted* refresh token) and the new
  post-`login()` `startSession()` call (a refresh token `login()` just
  minted), on the theory that both needed the identical
  `{"tokens": {...}}` shape. That theory was wrong: passing that shape
  (with blank placeholder `token`/`accessToken` values, which the *resume*
  path deliberately uses to force a refresh) back through `startSession()`
  right after a fresh `login()` actually **overwrites the real, already-valid
  tokens `login()` just obtained** with those blanks — `updateTokens()`
  applies them unconditionally, and `tokenCreated` is too fresh for the 90%-
  expiry refresh check to repair it. An earlier version of this fix also
  tried calling `hive.getDevices()` directly instead (to avoid touching
  tokens at all), which turned out to be incomplete: `deviceList` is built
  by a *separate* method, `createDevices()`, that `getDevices()` alone never
  calls. The implemented fix instead passes an **explicitly empty `{}`**
  config to the existing, already-tested `startSession()`/`_start_session`
  path: an empty config skips token/username/password/device_data
  processing entirely (nothing to set — tokens are already valid) and falls
  straight through to that method's own correct `getDevices()` +
  `createDevices()` sequence. No `_tokens_config` builder exists in the
  final implementation; `_resume_config` was left as it already was,
  building its own `{"tokens": {...}, "device_data": (...)}` shape directly.

- **No change** to: `HiveAuthenticator` (its `authenticate()` branching on
  `read_auth_state()` being `None` vs. not is unaffected — this fix is
  entirely inside what `HiveApiSource._establish_session`/`_login` do once
  called), `_fetch_heating_status`'s own use of `_establish_session` (it
  already passes the real `state` value, benefiting from the same fix
  automatically), `_resume`'s branch (the resume path's existing behaviour
  and its own tests are untouched), the `hive_auth_state.json` persistence
  format, ADR-0019, or any config/schema.

## Testing Decisions

- **Seam**: mock `apyhiveapi`'s `Hive` class directly — the real system
  boundary between this repo's code and the third-party library, per
  `.agent-docs/agent.md`'s "mock only at system boundaries" rule. Not the
  existing fake-`HiveSource` convention (`test_hive_authentication.py`'s
  `_FakeAuthHiveSource`) — that fake never touches real `apyhiveapi` at all,
  so it structurally cannot exercise (or have caught) this exact bug class.
  Not boto3/Cognito's own HTTP boundary underneath `apyhiveapi` either — that
  would mean re-simulating `apyhiveapi`'s own internal challenge-routing
  logic, code this repo doesn't own and shouldn't be re-testing on its
  behalf.
- Fake/stub `Hive`-shaped test doubles return the exact dict shapes the real
  library returns, copied from its own source read this session (e.g.
  `{"AuthenticationResult": {"IdToken": ..., "AccessToken": ...,
  "RefreshToken": ..., "ExpiresIn": ...}}` for success,
  `{"ChallengeName": "SMS_MFA"}` for the interactive case) — not invented
  approximations.
- Cover: fresh login succeeds (asserts `login()` then `startSession()` are
  both called, in that order, with the right config shape on the second
  call); fresh login hits the SMS-required challenge (asserts
  `HiveReauthRequired` raised with `_LOGIN_REQUIRES_SMS_MESSAGE`, not a raw
  `KeyError` or anything else); the existing resume-path tests continue to
  pass unmodified (this fix doesn't touch that branch).
- No live Hive account access during implementation or its automated tests —
  the user will test the real login once, deliberately, after this fix is
  deployed, not as part of this loop.

## Out of Scope

- Any change to the resume path (`_resume`/`_resume_config`'s own behaviour
  for an *existing* persisted auth state) beyond extracting the shared
  tokens-config builder described above — its logic and tests are unchanged.
- Handling `DEVICE_VERIFIER_CHALLENGE`/device-registration explicitly in
  `HiveApiSource` — `login()` already handles that internally
  (`_handleDeviceLoginChallenge`) and only surfaces to the caller as either
  plain success or (if the device turns out not to be remembered) the
  existing `HiveReauthRequired` path via `HiveInvalidDeviceAuthentication`
  bubbling up unhandled, same as today.
- Actually completing a live SMS 2FA flow (e.g. an interactive code-entry
  mechanism) — out of scope per the spec's own design ("a headless service
  cannot supply one"); the fix's job is to fail cleanly and alert, not to
  make 2FA possible unattended.
- The live Pi redeploy/real-account retest — the user does this manually
  after merge, not as part of this implementation loop.

## Further Notes

Discovered and root-caused live this session (2026-09-28) during hive-app's
first deployment to the Pi, while working issue #533 (ntfy topic
configuration). Full source-reading notes are in this session's `/design`
transcript; the key facts (the real `apyhiveapi==1.0.9` `HiveSession.login`/
`startSession`/`updateTokens` behaviour) came from reading the actual
installed library source directly, not from its README or prior assumptions
in this codebase's own comments (one of which — `_resume_config`'s docstring
— turned out to itself be a stale, unverified assumption).

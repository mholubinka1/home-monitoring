# Fix: hive-app's ntfy re-auth alert never fires on a real Hive auth failure

## Problem Statement

`hive-app`'s ntfy alert (ADR-0018) exists for exactly one scenario: Hive's Cognito session can no longer be resumed and recovering needs a live human to complete an SMS 2FA login, something a headless container cannot do itself. Without the alert, this failure is silent — `job_run` rows pile up with `heating_refresh` failures, visible only to someone who happens to check Grafana.

On 2026-10-01, during an unrelated database cutover, `hive-app`'s persisted auth state turned out to be invalid. The app raised `apyhiveapi.helper.hive_exceptions.HiveInvalidDeviceAuthentication` (Cognito "device resource not found") on every resume attempt, both at container startup and on every subsequent scheduled `heating_refresh` poll — and the ntfy alert never fired, despite ntfy being correctly configured and independently verified working. The failure was discovered only by chance, while reviewing logs for an unrelated reason.

Root cause: `apps/hive-app/hive_app/data/hive_client.py`'s `_start_session` (the resume path) only catches `apyhiveapi`'s own `HiveReauthRequired` and translates it into this repo's `hive_app.common.exceptions.HiveReauthRequired` — the only exception type `HeatingRetriever._notify_reauth_required()` (the ntfy alert path, in `apps/hive-app/hive_app/data/heating.py`) responds to. `apyhiveapi` defines several sibling exceptions as plain, unrelated `Exception` subclasses — not subclasses of its own `HiveReauthRequired` — including `HiveInvalidDeviceAuthentication`, `HiveAuthError`, `HiveFailedToRefreshTokens`, `HiveInvalid2FACode`, and `HiveUnknownConfiguration`. Any of these propagates uncaught past `_start_session`'s narrow `except` clause. Separately, `main.py`'s `authenticate_at_startup` catches any exception at container startup but only logs `CRITICAL` — it never touches the notifier at all, regardless of exception type, so even an already-correctly-mapped `HiveReauthRequired` raised at startup would not alert until the next scheduled poll (every 120 seconds) picked it up.

## Solution

Every `apyhiveapi` exception that genuinely means "a live human must complete re-login" gets translated to this repo's `HiveReauthRequired` at both places `hive_client.py` talks to `apyhiveapi` (the resume path and the fresh-login path), so the existing ntfy alert and de-duplication logic (ADR-0018) actually fires for every real occurrence of the scenario it was built for — not just the one exception type it happened to be tested against. A startup auth failure alerts immediately, without waiting for the first scheduled `heating_refresh` poll.

Actually completing a live re-login (the SMS 2FA step itself) remains a manual, interactive, outside-the-container action — no code in this repo can perform it unattended, and none is added here. This fix guarantees the alert fires reliably when that manual action is needed; it does not build tooling to perform the action itself.

## User Stories

1. As the household member who owns this Pi, I want to be notified promptly whenever Hive's heating integration needs a live re-login, regardless of which specific `apyhiveapi` exception the failure happens to surface as, so that I don't discover the heating integration has been silently dead for hours or days by chance.
2. As that same person, I want to be notified at container startup if auth is already broken, rather than waiting up to 120 seconds for the first scheduled poll to notice, so that a Pi reboot with stale auth state alerts as promptly as a mid-run failure does.
3. As a maintainer reading `hive_client.py`, I want the full set of "this means re-auth is needed" exceptions named in one place, so that extending or auditing this behavior later doesn't require re-deriving which `apyhiveapi` exceptions actually mean this from documentation or trial and error.
4. As the person who just hit this exact failure on the live Pi, I want a clear note on how to actually complete the live re-login once the alert fires, since no part of this app can do that step for me.

## Implementation Decisions

- **Shared exception set**: a single module-level tuple in `hive_client.py` (e.g. `_REAUTH_REQUIRED_EXCEPTIONS`) listing every `apyhiveapi` exception that means "needs a live human re-login": `ApyHiveReauthRequired`, `HiveInvalidDeviceAuthentication`, `HiveAuthError`, `HiveFailedToRefreshTokens`, `HiveInvalid2FACode`, `HiveUnknownConfiguration`. `HiveInvalidUsername`/`HiveInvalidPassword` are deliberately excluded — a config-time credential error, not this scenario, and conflating the two would mislabel a typo'd password as "needs live re-login."
- **Two call sites, one mapping**: both `_start_session` (resume path, today only catching `ApyHiveReauthRequired`) and `_fresh_login` (fresh-login path, today letting these exceptions propagate raw per its own docstring) catch this shared tuple and raise this repo's `HiveReauthRequired` from it.
- **Shared de-dup/notify component**: the `_reauth_notified` flag and notify-once-per-incident logic currently private to `HeatingRetriever` (`heating.py`) is extracted into its own small class, constructed once in `main()` and passed to both `HiveAuthenticator` and `HeatingRetriever`. This prevents a startup alert from being immediately duplicated by the next scheduled `heating_refresh` poll, which would otherwise re-raise the same still-unresolved failure against its own, independently-tracked de-dup state. A successful authenticate/refresh clears the shared flag, exactly as `HeatingRetriever.refresh()` already does today.
- **Startup auth notifies via `HiveAuthenticator`**: `HiveAuthenticator.authenticate()` (which receives the shared component) calls the same notify-once logic `HeatingRetriever` uses on a `HiveReauthRequired`, then re-raises; `authenticate_at_startup` keeps logging `CRITICAL` as before. A non-`HiveReauthRequired` exception at startup keeps today's log-only behavior — this fix is scoped to the re-auth scenario, not startup error handling in general.
- **No ADR**: this is a direct consequence of the shared-de-dup requirement, not a new architectural trade-off — documented via this spec and the shared component's own docstring (matching how `_notify_reauth_required`'s existing docstring already explains its own reasoning).
- **Operational note, not tooling**: the spec's acceptance criteria include documenting — in a comment or a short note near `HiveAuthenticator`/`main.py`, not a new script — that completing a live re-login requires an interactive SMS 2FA step this codebase has no mechanism to perform unattended, and that the currently-broken live Pi auth needs this manual step regardless of this fix.

## Testing Decisions

- **Mock only at the two real boundaries**: `apyhiveapi`'s `Hive` object (patch `hive.startSession`/`hive.login` to raise each exception under test) and the ntfy HTTP call (via `responses`, matching `test_ntfy_reauth_notifier.py`/`test_reauth_notifier_wiring.py`'s existing pattern). Everything else — `HiveApiSource`, `HeatingRetriever`, `HiveAuthenticator`, the shared de-dup component — is exercised through real wiring.
- **Exception-mapping coverage**: a parametrized test asserting each exception in the shared tuple, raised from a mocked `hive.startSession()` during resume, is translated to this repo's `HiveReauthRequired` — not just the one exception (`ApyHiveReauthRequired`) already covered. Per the existing "single test for a branch with several triggers" standard, each distinct exception type gets its own assertion, not one combined test for the branch as a whole.
- **Fresh-login path coverage**: the same exception set, raised from a mocked `hive.login()`, also translated correctly — this path currently has no such mapping at all (per its own docstring), so this is new coverage, not a regression test.
- **Shared de-dup coverage**: a test wiring one shared component to both a `HiveAuthenticator` whose `authenticate()` fails and a `HeatingRetriever` whose `refresh()` is then called immediately after — asserting exactly one ntfy notification fires across both calls, not two. A second test confirms a later, successful `refresh()` clears the flag so a subsequent, distinct incident notifies again.
- **Startup-notifies test**: `authenticate_at_startup` (or its restructured equivalent) triggers a notification on a `HiveReauthRequired` failure, verified via the same `responses`-mocked ntfy endpoint — today's behavior (log-only) has no such test, since no such behavior exists yet.

## Out of Scope

- Building any mechanism to complete a live SMS 2FA login unattended — fundamentally impossible for a headless service per ADR-0018 and the Hive API research doc.
- Re-establishing the live Pi's actual broken Hive auth — a manual, interactive, one-off operational action, noted but not performed as part of this fix.
- `HiveInvalidUsername`/`HiveInvalidPassword` — genuinely different failure modes (bad configured credentials), not this scenario.
- Any change to `job_run`/Grafana's existing passive failure-visibility pattern — ntfy stays scoped to this one alert, per ADR-0018.
- A formal ADR for the de-dup-extraction — judged a direct consequence of the shared-state requirement, not a new trade-off worth recording separately.

## Further Notes

Discovered during the live `#547` Pi cutover (2026-10-01) while checking `hive-app`'s restart logs for unrelated verification purposes — the cutover itself is unaffected by and unrelated to this bug; it only made an already-existing, already-silent failure visible by chance.

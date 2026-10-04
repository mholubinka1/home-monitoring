# hive-app: a Hive API timeout is not a re-authentication requirement

## Problem Statement

The operator receives two ntfy notifications in quick succession ("re-authentication required", then "authentication recovered" about a minute later) without doing anything. Five such pairs occurred between 2026-10-03 20:01 and 2026-10-04 12:34. Each had the same cause: Hive's cloud API timed out listing devices, and hive-app reported that as "Hive needs a live SMS 2FA code". Authentication was never broken. The alert text is false and the runbook's SMS login would not help.

## Solution

hive-app tells a Hive API timeout apart from a genuine re-authentication requirement. A timeout is an ordinary transient poll failure: it is retried with backoff and recorded as a failed `job_run`, but it never sends the "re-authentication required" ntfy alert. Genuine re-authentication failures alert exactly as before.

## User Stories

1. As the operator, I want a Hive API timeout not to page me with "re-authentication required", so that I am only alerted when a live SMS login is really needed.
2. As the operator, I want genuine re-authentication failures (device not remembered, SMS challenge, token refresh failure) to alert as before, so that a real incident is never missed.
3. As the operator, I want timeouts still retried and recorded in `job_run`, so that outages remain visible on the dashboard.
4. As the operator, I want no "authentication recovered" notice after a timeout, so that I get no noise from self-healing blips.

## Implementation Decisions

- apyhiveapi swallows the API's `asyncio.TimeoutError` in `getDevices` and returns normally; with a fresh `Hive` per poll there is no cached data, so `startSession` then raises a bare `HiveReauthRequired` for the empty device list. That exception cannot be told apart from a genuine one by type, cause or device state (genuine ones are also raised bare), so the timeout is observed directly.
- `HiveApiSource` records an `asyncio.TimeoutError` from the session's API `getAll` call (re-raising it unchanged) for the duration of one session.
- When `startSession` raises apyhiveapi's `HiveReauthRequired` and a timeout was recorded in the same session, a new `HiveApiUnavailable` exception (this repo's own type, not a `HiveReauthRequired`) is raised instead. Every other path still raises `HiveReauthRequired`.
- `HiveApiUnavailable` is handled by the existing generic failure path (retry with backoff, failed `job_run`); `ReauthAlert` never sees it.
- Document in ADR-0018 that a timeout is not a re-auth trigger and why it must be observed rather than inferred; update the `HiveReauthRequired` docstring accordingly.
- Only apyhiveapi's own `HiveReauthRequired` is reclassified; the other re-login exceptions still alert after a timeout. Startup login and resume share this handling, so a timeout there is also transient (startup only logs non-reauth failures).
- No config, schema or alert-format changes. Alert timing is unchanged.

## Testing Decisions

- Seam: the existing real-`HiveApiSource` seam in `test_hive_reauth_exception_mapping.py`, with a fake apyhiveapi `Hive` (the system boundary) and `responses` for ntfy.sh, driven through `HeatingRetriever.refresh`.
- Scenarios: timeout then empty-device `HiveReauthRequired` yields `HiveApiUnavailable` and no ntfy POST; a genuine `HiveReauthRequired` with no timeout still yields `HiveReauthRequired` and one POST; a timeout does not affect a later session's classification.
- Prior art: `test_hive_reauth_exception_mapping.py`, `test_reauth_alert_wiring.py`.

## Out of Scope

- Retry count, delays or poll interval; alert timing or delaying alerts until retries are exhausted.
- Changing apyhiveapi, or classifying other API failures.
- The residual case of a genuine bare `HiveReauthRequired` coinciding with a timeout in the same call (the next poll still alerts).

## Further Notes

Evidence: all five alerts were preceded by "Hive API request timed out fetching all nodes." and "No devices or products returned from Hive API, reauthentication required." in `docker logs hive-app`, and every one recovered on the first 60-second retry.

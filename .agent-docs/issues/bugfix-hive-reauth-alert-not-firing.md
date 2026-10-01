# Issues: bugfix-hive-reauth-alert-not-firing

## hive-app: map all re-auth-needed apyhiveapi exceptions to HiveReauthRequired — [#555](https://github.com/mholubinka1/home-monitoring/issues/555)

> Work complete — [PR #557](https://github.com/mholubinka1/home-monitoring/pull/557) ready to merge.

**Blocked by**: None

**User stories**: 1, 3

### What to build

`hive_client.py`'s `_start_session` (resume) only translates apyhiveapi's
`HiveReauthRequired`; `_fresh_login` lets everything propagate raw.
apyhiveapi's sibling exceptions that also mean "a live human must re-login"
(`HiveInvalidDeviceAuthentication`, `HiveAuthError`,
`HiveFailedToRefreshTokens`, `HiveInvalid2FACode`, `HiveUnknownConfiguration`)
therefore bypass the ntfy alert. Add one module-level tuple naming the full
set (including `ApyHiveReauthRequired`; deliberately excluding
`HiveInvalidUsername`/`HiveInvalidPassword`) and have both the resume and
fresh-login paths translate any of them to this repo's `HiveReauthRequired`,
so a scheduled `heating_refresh` poll hitting any of them fires the existing
ntfy alert.

### Acceptance criteria

- [x] Given `hive.startSession()` raises any exception in the set during
      resume, then this repo's `HiveReauthRequired` is raised (one assertion
      per exception type).
- [x] Given `hive.login()` raises any exception in the set during a fresh
      login, then this repo's `HiveReauthRequired` is raised (one assertion
      per exception type).
- [x] Given `HiveInvalidUsername`/`HiveInvalidPassword`, then they are not
      translated.
- [x] Given a `HeatingRetriever` poll fails with a mapped exception other than
      `ApyHiveReauthRequired`, then exactly one ntfy POST is made (ntfy
      mocked via `responses`).

---

## hive-app: share reauth de-dup across startup and polling, and alert at startup — [#556](https://github.com/mholubinka1/home-monitoring/issues/556)

> Work complete — [PR #557](https://github.com/mholubinka1/home-monitoring/pull/557) ready to merge.

**Blocked by**: #555

**User stories**: 2, 4

### What to build

The notify-once-per-incident logic (`_reauth_notified` flag and
`_notify_reauth_required`) is private to `HeatingRetriever`, and
`authenticate_at_startup` only logs `CRITICAL` on failure, so a startup
re-auth failure never alerts until the first scheduled poll, and if it did
alert, the next poll would alert again from independent state. Extract the
flag and notify logic into its own small class (docstring carries the
ADR-0018 reasoning), construct it once in `main()`, and pass it to both
`HiveAuthenticator` and `HeatingRetriever`. `HiveAuthenticator.authenticate()`
notifies through it on `HiveReauthRequired` (and re-raises);
`authenticate_at_startup` still just logs, and other startup exceptions stay
log-only. A successful authenticate/refresh clears the shared flag. Also
document, in a comment near `HiveAuthenticator`/`main.py`, that completing a
live re-login needs an interactive SMS 2FA step this codebase cannot perform
unattended.

### Acceptance criteria

- [x] Given `authenticate()` fails with `HiveReauthRequired` at startup, then
      one ntfy notification is sent immediately.
- [x] Given that startup failure followed immediately by a failing
      `HeatingRetriever.refresh()`, then exactly one ntfy notification fires
      across both.
- [x] Given a later successful `refresh()`, then the flag clears and a
      subsequent distinct incident notifies again.
- [x] Given a failed notification delivery, then the flag is not set and the
      next failure retries.
- [x] Given a non-`HiveReauthRequired` startup exception, then behaviour is
      unchanged (log-only, no notification).
- [x] Given ntfy is not configured, then no HTTP call is made.
- [x] The manual SMS 2FA re-login note exists near
      `HiveAuthenticator`/`main.py`.

---

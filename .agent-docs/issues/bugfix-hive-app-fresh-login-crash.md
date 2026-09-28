# Issues: bugfix-hive-app-fresh-login-crash

## hive-app: fix fresh Hive login crashing instead of calling apyhiveapi's real login() — [#539](https://github.com/mholubinka1/home-monitoring/issues/539)

> Work complete — [PR #540](https://github.com/mholubinka1/home-monitoring/pull/540) ready to merge.

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

`HiveApiSource._establish_session`'s fresh-login branch (no persisted auth
state) currently calls `hive.startSession()` with no arguments, which the
real `apyhiveapi==1.0.9` library does not treat as a login trigger -- it
silently skips straight to a token refresh with no tokens to refresh,
crashing with `KeyError: 'refreshToken'`. Fix the fresh-login branch to call
`hive.login()` (apyhiveapi's actual login entry point) instead: on success,
follow up with a `startSession()` call (using a shared tokens-config builder
factored out of the existing `_resume_config`) to populate devices; on the
one interactive case a headless service can't complete (SMS 2FA required,
no remembered device), raise this repo's own `HiveReauthRequired` using the
existing (currently unused) `_LOGIN_REQUIRES_SMS_MESSAGE` constant, so the
existing ntfy re-auth alert fires. Also correct `_resume_config`'s docstring
comment, which makes the same incorrect assumption about `startSession()`'s
behaviour that caused this bug in the first place. The resume path
(`_resume`/`_resume_config`'s own logic) is unchanged.

### Acceptance criteria

- [x] Given a Hive account needing no interactive login step, when hive-app
      performs its first-ever login (no persisted auth state), then it
      completes successfully via `hive.login()` and a follow-up
      `hive.startSession()` populates devices, with no unhandled exception.
- [x] Given a Hive account that needs a live SMS 2FA code and has no
      remembered device, when hive-app performs its first-ever login, then
      it raises this repo's `HiveReauthRequired` with the existing
      `_LOGIN_REQUIRES_SMS_MESSAGE` text (triggering the existing ntfy
      alert), not a raw `KeyError` or any other unhandled exception.
- [x] The resume path (`_resume`/`_resume_config` for an *existing* persisted
      auth state) is unchanged in behaviour, and its existing tests continue
      to pass unmodified.
- [x] Tests mock `apyhiveapi`'s `Hive` class directly (the real system
      boundary, per `.agent-docs/agent.md`) with response shapes matching
      the real library's actual source -- not the existing fake-`HiveSource`
      convention, which cannot exercise this bug class, and not a
      boto3/Cognito-level mock, which would re-simulate library-internal
      logic this repo doesn't own.
- [x] No test or implementation step touches a real Hive account/credentials.

---

# Issues: bugfix-hive-login-direct-device-registration

## hive-app: login command registers the device when Hive logs in without an SMS challenge — [#568](https://github.com/mholubinka1/home-monitoring/issues/568)

**Blocked by**: None

**User stories**: 1, 2, 3, 4

### What to build

Fix `python -m hive_app.login` for the case where Hive logs in directly (no
SMS challenge) and returns a new device to remember. Live evidence: the
command exited 1 within seconds with "did not yield a refresh token and
remembered device", no SMS prompt, nothing saved. Cause (apyhiveapi
`HiveAuthAsync.login`): a keyless login can return `AuthenticationResult`
with `NewDeviceMetadata`; `login()` stores the device group key and device
key but only `device_registration()` generates the device password, and
`_interactive_login` called it only on the SMS branch. After a direct
`AuthenticationResult`, if the session holds a device key but no device
password, call `device_registration()` before starting the session. Keep the
completeness check; with no device metadata the command still refuses and
persists nothing. Update the runbook to say an SMS code is requested only if
Hive sends the challenge.

### Acceptance criteria

- [ ] Given a direct login that returns device metadata, the device is
      registered and the full resume tuple is returned (call order login,
      device_registration, startSession; no code requested).
- [ ] Given a direct login with no device metadata, the command still raises
      the remembered-device error and persists nothing.
- [ ] Given the command on the direct-login path, it writes the auth state
      file and returns 0, and a fresh HiveApiSource resumes from it with the
      saved credentials.
- [ ] The runbook says an SMS code is requested only if Hive sends the
      challenge.

---

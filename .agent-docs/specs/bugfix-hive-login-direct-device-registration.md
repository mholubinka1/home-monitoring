# hive-app login command: register the device when Hive logs in without an SMS challenge

## Problem Statement

`python -m hive_app.login` (the recovery command behind the "re-authentication required" ntfy alert) fails on the live account. Run against the real Hive account, it exited 1 within seconds with "Hive login completed but did not yield a refresh token and remembered device", never prompted for an SMS code, and saved nothing. The operator therefore has no working way to recover hive-app, whose persisted remembered device Cognito no longer recognises ("device resource not found").

## Solution

When Hive logs in directly, with no SMS challenge, and hands back a new device to remember, the command confirms that device (the same registration step the SMS path already performs) before starting the session. The command then saves a complete, resumable auth state. If the login yields no device to remember at all, it still refuses to save anything and says why.

## User Stories

1. As the operator, I want the recovery command to work when Hive does not ask for an SMS code, so that I can recover hive-app whichever way Hive authenticates me.
2. As the operator, I want the command to register the new device in that case, so that the saved state lets hive-app resume after a restart without logging in again.
3. As the operator, I want the command to still refuse to save an incomplete state, so that a half-built login can never replace the stored one.
4. As the operator, I want the runbook to say that an SMS code is only requested if Hive sends the challenge, so that a missing prompt does not look like a failure.

## Implementation Decisions

- **Cause (from apyhiveapi's `HiveAuthAsync.login`).** With no device key sent, the password-verifier response can contain `AuthenticationResult` together with `NewDeviceMetadata`. `login()` then records the device group key and device key but not the device password; only `device_registration()` (confirm the device, then update its status) generates the password. `_interactive_login` called `device_registration()` only on the SMS branch.
- **Fix.** After a direct `AuthenticationResult`, if the session holds a device key but no device password, call `hive.auth.device_registration()` before starting the session. The SMS branch is unchanged. The final completeness check (refresh token, device group key, device key, device password all present) stays, so a login that yields no device metadata still raises the existing "remembered device" error and persists nothing.
- **Runbook.** `REAUTH_RUNBOOK.md` Recover steps say the command prompts for a code only if Hive sends the SMS challenge, and the Verify section is unchanged. The failure table stays accurate.
- **Fake `Hive`.** The test fake's `login()` mirrors the real one for a direct `AuthenticationResult`: it records tokens and, when present, the device group key and device key from `NewDeviceMetadata`. Its `device_registration()` already sets the device password.

## Testing Decisions

- Tests assert observable behaviour at the existing seams: `HiveApiSource.interactive_login` for the flow and `hive_app.login.main` for the command end to end. Prior art: `test_hive_interactive_login.py` and `test_hive_login_command.py`.
- Cases: a direct login with device metadata registers the device and returns the full tuple (call order login, device_registration, startSession; the code provider is never called); a direct login with no device metadata still raises and persists nothing (existing tests); the command on this path writes the state file and returns 0, and a fresh `HiveApiSource` resumes from it with the saved credentials.
- The diagnosis comes from the library source and one failed live run. A read-only probe against the live account, run by the operator, can confirm it; the fix is written to be correct for either outcome the probe could show (direct login with metadata, or an SMS challenge).

## Out of Scope

- Deploying the fix or running the login on the Pi.
- Changing the SMS-branch behaviour, the notifications, or how the app resumes.
- Retrying or reusing a stale persisted device.

## Further Notes

- Found by running the command on the live account right after PR #563 merged. The completeness check added in that PR's Copilot review is what prevented a broken state from being saved.

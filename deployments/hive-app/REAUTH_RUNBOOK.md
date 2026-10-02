# hive-app runbook: recovering from "re-authentication required"

hive-app sends the ntfy alert **hive-app: re-authentication required** when Hive no longer recognises its remembered device and a fresh interactive login is needed, which may involve a live SMS 2FA code (see [ADR-0018](../../.agent-docs/adr/0018-ntfy-for-hive-reauth-alerting.md)). Until you complete the login below, heating polling keeps failing and `job_run` shows the failures. A headless container cannot read an SMS, so this is an interactive step you run on the Pi.

## Recover

1. Have the phone for the Hive account to hand. Hive may text an SMS 2FA code as soon as the login starts, and it expires quickly; it may also log you in with no SMS at all (see step 3).

2. On the Pi, start the login inside the running container:

   ```bash
   docker exec -it hive-app python -m hive_app.login --config-file /config/config.yml
   ```

   Use `-it`: if Hive sends the SMS challenge, the command prompts for the code with hidden input and needs a terminal.

3. Enter the SMS code if prompted. The command prompts for a code only if Hive sends the SMS challenge; if Hive logs in with no challenge, it registers a new device and saves the state without any prompt. Either way, on success the command has registered a device with Hive and written `hive_auth_state.json` to `/mnt/media/pi-media/containers/hive-app/config/` (the container's `/config`), then prints a success message and exits `0`.

4. **Do not restart the container.** The running app re-reads `hive_auth_state.json` on every heating poll (every 120 seconds), so it recovers on its own within about two minutes. The "required" alert is remembered in memory only, so a restart before that first successful poll would forget it and no "authentication recovered" notification would be sent (recovery itself would still work).

## Verify

- Within about two minutes ntfy delivers **hive-app: authentication recovered** (only if the "required" alert was actually delivered earlier by the same running process).
- `docker logs hive-app` shows the heating poll succeeding again with no `HiveReauthRequired`.
- `job_run` shows the heating refresh succeeding again.

If you did restart before the first successful poll, or the container restarted for another reason in the meantime, expect no "recovered" notification; the log and `job_run` checks above still confirm recovery.

## If the command fails

| Output | Meaning | Fix |
| --- | --- | --- |
| `Hive login failed: HiveInvalid2FACode` (exit 1) | The code was wrong or had expired | Run the command again and use the fresh code from the new SMS |
| `Hive login failed: RuntimeError: ... neither an AuthenticationResult nor an SMS_MFA challenge` (exit 1) | Hive returned a login step this tool does not handle | Do not retry in a loop; check the Hive app and account for a pending security prompt, then open an issue with the message |
| `Hive login failed: RuntimeError: Hive login completed but did not yield a refresh token and remembered device ...` (exit 1) | The login succeeded but Cognito offered no remembered device (or no refresh token), so a restart would need another SMS code; nothing was saved | Run the command again. If it repeats, check the account's device-remembering setting in Hive/Cognito and open an issue with the message |
| Non-zero exit loading the config | `/config/config.yml` is missing or invalid | Fix the file on the Pi; see [config.yml.template](config.yml.template) for the shape |
| `Hive login failed: PermissionError: ...` (exit 1) | The login itself succeeded, but `/config` is not writable by UID 999, so the state could not be saved | `chown` the host directory to UID 999 (or an equivalent ACL), as noted in `deployments/hive-app/docker-compose.yml`, then run the command again with a fresh SMS code |

A failed run never overwrites an existing `hive_auth_state.json`.

## The notifications

hive-app sends exactly two ntfy notifications about Hive auth, both to the secret topic configured in `config.yml` (format in the [README](../../README.md#ntfy-notifications-hive-app)):

| Notification | Title | Priority | Tags | Click |
| --- | --- | --- | --- | --- |
| Re-auth required | `hive-app: re-authentication required` | `high` | `warning,key` | this runbook |
| Auth recovered | `hive-app: authentication recovered` | `default` | `white_check_mark` | none |

## Why this is manual

Cognito can skip repeat logins only for a device it remembers. Once it forgets that device (for example after a long idle period), a new device has to be registered through a fresh interactive login, which Hive may gate behind a live SMS code. Registering a device is a deliberate operator action, because the saved device keys can obtain Hive tokens without the password or an SMS ([ADR-0019](../../.agent-docs/adr/0019-file-based-hive-auth-state-storage.md)), so there is no unattended path by design.

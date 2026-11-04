# hive-app runbook: Hive data lost

hive-app sends the ntfy alert **hive-app: Hive data lost** when no new heating status has arrived for 30 minutes (see [ADR-0031](../../.agent-docs/adr/0031-hive-data-lost-alert-replaces-the-reauth-alert.md)). The message names the latest failed poll reason. Most outages heal by themselves; wait for the hourly reminder before acting. If the reason says Hive's persisted session can no longer be resumed, Hive no longer recognises the remembered device and a fresh interactive login is needed, which may involve a live SMS 2FA code: follow **Recover** below. For any other reason (for example `'NoneType' object has no attribute 'get'`, or a Hive API timeout), check `docker logs hive-app` and `job_run`, and Hive's own status. A headless container cannot read an SMS, so the login is an interactive step you run on the Pi.

## Recover

1. Have the phone for the Hive account to hand. Hive may text an SMS 2FA code as soon as the login starts, and it expires quickly; it may also log you in with no SMS at all (see step 3).

2. On the Pi, start the login inside the running container:

   ```bash
   docker exec -it hive-app python -m hive_app.login --config-file /config/config.yml
   ```

   Use `-it`: if Hive sends the SMS challenge, the command prompts for the code with hidden input and needs a terminal.

3. Enter the SMS code if prompted. The command prompts for a code only if Hive sends the SMS challenge; if Hive logs in with no challenge, it registers a new device and saves the state without any prompt. Either way, on success the command has registered a device with Hive and written `hive_auth_state.json` to `/mnt/media/pi-media/containers/hive-app/config/` (the container's `/config`), then prints a success message and exits `0`.

4. **Do not restart the container.** The running app re-reads `hive_auth_state.json` on every heating poll (every 120 seconds), so it recovers on its own within about two minutes. The "lost" alert is remembered in memory only, so a restart before that first successful poll would forget it and no "restored" notification would be sent (recovery itself would still work).

## Verify

- Within about three minutes ntfy delivers **hive-app: Hive data restored** (only if the "lost" alert was actually delivered earlier by the same running process, and outside quiet hours; otherwise it arrives when they end).
- `docker logs hive-app` shows the heating poll succeeding again with no `HiveReauthRequired`.
- `job_run` shows the heating refresh succeeding again.

If you did restart before the first successful poll, or the container restarted for another reason in the meantime, expect no "restored" notification; the log and `job_run` checks above still confirm recovery.

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

hive-app sends these ntfy notifications about Hive data, all to the secret topic configured in `config.yml` (format and quiet hours in the [README](../../README.md#ntfy-notifications-hive-app)):

| Notification | Title | Priority | Tags | Click |
| --- | --- | --- | --- | --- |
| Data lost | `hive-app: Hive data lost` | `high` | `warning` | this runbook |
| Still lost (hourly) | `hive-app: Hive data still lost` | `high` | `warning` | this runbook |
| Data restored | `hive-app: Hive data restored` | `default` | `white_check_mark` | none |

## Why this is manual

Cognito can skip repeat logins only for a device it remembers. Once it forgets that device (for example after a long idle period), a new device has to be registered through a fresh interactive login, which Hive may gate behind a live SMS code. Registering a device is a deliberate operator action, because the saved device keys can obtain Hive tokens without the password or an SMS ([ADR-0019](../../.agent-docs/adr/0019-file-based-hive-auth-state-storage.md)), so there is no unattended path by design.

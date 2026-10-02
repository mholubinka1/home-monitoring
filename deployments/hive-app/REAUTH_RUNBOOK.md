# hive-app runbook: recovering from "re-authentication required"

hive-app sends the ntfy alert **hive-app: re-authentication required** when Hive no longer recognises its remembered device and a live SMS 2FA code is needed (see [ADR-0018](../../.agent-docs/adr/0018-ntfy-for-hive-reauth-alerting.md)). Until you complete the login below, heating polling keeps failing and `job_run` shows the failures. A headless container cannot read an SMS, so this is an interactive step you run on the Pi.

## Recover

1. Make sure the Hive account's SMS 2FA code can reach you. The code is texted to the phone number on the Hive account as soon as the login starts, and it expires quickly, so have the phone to hand before you run the command.

2. On the Pi, start the login inside the running container:

   ```bash
   docker exec -it hive-app python -m hive_app.login --config-file /config/config.yml
   ```

   Use `-it`: the command prompts for the code with hidden input and needs a terminal.

3. Enter the SMS code when prompted. On success the command registers the device with Hive and writes `hive_auth_state.json` to `/mnt/media/pi-media/containers/hive-app/config/` (the container's `/config`), then prints a success message and exits `0`.

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

Cognito only skips SMS 2FA for a device it remembers. Once it forgets that device (for example after a long idle period), only a live SMS code can register a new one. There is no unattended path by design.

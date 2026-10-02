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

4. Restart the container so startup auth resumes from the new state straight away, rather than waiting for the next poll:

   ```bash
   docker compose -f /home/pi/git/pi-desktop/docker/docker-compose.yml restart hive-app
   ```

   (adjust the compose path and service name to the Pi's live setup).

## Verify

- ntfy delivers **hive-app: authentication recovered** after the next successful heating poll, but only if the "required" alert was actually delivered earlier.
- `docker logs hive-app` shows "Persisted Hive auth state found; resuming via refresh." with no `HiveReauthRequired` after it.
- `job_run` shows the heating refresh succeeding again.

## If the command fails

| Output | Meaning | Fix |
| --- | --- | --- |
| Non-zero exit with "invalid 2FA code" | The code was wrong or had expired | Run the command again and use the fresh code from the new SMS |
| Non-zero exit with "unexpected challenge" | Hive returned a login step this tool does not handle | Do not retry in a loop; check the Hive app and account for a pending security prompt, then open an issue with the message |
| Non-zero exit loading the config | `/config/config.yml` is missing or invalid | Fix the file on the Pi; see `config.yml.template` for the shape |
| `Permission denied` writing the auth state | `/config` is not writable by UID 999 | `chown` the host directory to UID 999 (or an equivalent ACL), as noted in `deployments/hive-app/docker-compose.yml` |

A failed run never overwrites an existing `hive_auth_state.json`.

## Why this is manual

Cognito only skips SMS 2FA for a device it remembers. Once it forgets that device (for example after a long idle period), only a live SMS code can register a new one. There is no unattended path by design.

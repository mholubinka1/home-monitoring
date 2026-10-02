# hive-app ntfy notifications: topic format, re-auth required/recovered, recovery runbook and login command

## Problem Statement

hive-app's only ntfy alert ("re-authentication required") is a bare plain-text POST to a free-form topic URL. It has no title, priority, tags or link. Its message says an interactive SMS 2FA login is needed, but no supported way to perform one exists: nothing in the repo accepts an SMS code and writes `hive_auth_state.json`, and no runbook documents recovery. The operator is also never told when Hive auth comes back. Separately, ntfy.sh topics are public, so a guessable topic name leaks alerts. Container config and logs are not consistently kept per container on pi-media: hive-app's template is still named `hive-config.yml.template` although the image reads `/config/config.yml`, hive-app has no log mount, and the shared logger writes to stdout only, so octopus-app's existing `/log` mount is unused.

## Solution

hive-app sends exactly two ntfy notifications on a topic named `home-monitoring-hive-auth-ntfy-<guid-no-dashes>`: **re-auth required** (high priority, links to a recovery runbook on `main`) and **auth recovered**. A new interactive login command lets the operator complete the SMS 2FA login on pi-media, and the runbook documents using it. Every container keeps its `config.yml` and its logs under `/mnt/media/pi-media/containers/<name>/{config,log}`, and the shared logger writes a rotating file there as well as stdout.

## User Stories

1. As the operator, I want the ntfy topic to be `home-monitoring-hive-auth-ntfy-<guid-no-dashes>`, so that the public ntfy.sh topic cannot be guessed.
2. As the operator, I want the GUID kept only in the Pi's gitignored `config.yml`, so that it is never committed.
3. As the operator, I want a re-auth-required notification with a clear title and high priority, so that I notice it and know it needs action.
4. As the operator, I want that notification to link to a runbook on this repo's `main` branch, so that I can recover with one tap.
5. As the operator, I want a runbook that documents the real recovery procedure, so that I am not following steps that do not exist.
6. As the operator, I want a login command I can run with `docker exec -it`, which prompts for the SMS code and writes the auth state, so that I can actually complete re-authentication.
7. As the operator, I want the login command to register and remember the device, so that later restarts resume without another SMS code.
8. As the operator, I want an "auth recovered" notification when Hive auth is working again, so that I know the heating client is alive.
9. As the operator, I want "recovered" sent only after a "required" alert was actually delivered, so that I am never told about a recovery I never heard was needed.
10. As the operator, I want only these two notifications, so that ntfy stays a narrow channel (ADR-0018) and poll failures stay on the dashboard.
11. As the operator, I want one "required" alert per incident, shared between startup auth and the poll, so that I am not paged repeatedly.
12. As the operator, I want a failed ntfy delivery to be retried on the next failure, and never to break polling, so that alerting problems cannot take the service down.
13. As the operator, I want each container's config at `containers/<name>/config/config.yml` and a matching template name in the repo, so that all containers are configured the same way.
14. As the operator, I want logs written to `containers/<name>/log/` on pi-media, so that they persist across container restarts and live beside the config.
15. As the operator, I want logging to fall back to console-only if `/log` is not writable, so that a missing mount never stops the app.
16. As a maintainer, I want the notification format documented, so that future alerts use the same conventions.

## Implementation Decisions

- **Topic.** The topic is `home-monitoring-hive-auth-ntfy-<32 lowercase hex chars>`. The config stays `ntfy.topic_url` holding the full URL. The repo template carries a placeholder and documents the format. The live GUID is set by the operator in the Pi's `config.yml`, and no real GUID is committed anywhere (including the spec, docs and tests).
- **Notification format** (documented in the runbook and context docs):
  - Title is `<app>: <short event>`, lowercase.
  - Body is one or two plain sentences, with no timestamp (ntfy adds one).
  - Priority is `high` for action needed and `default` for recovery.
  - Tags are a status emoji plus optionally one context tag: `warning,key` for required and `white_check_mark` for recovered.
  - `Click` is set only when a useful link exists.
- **Re-auth required:** title `hive-app: re-authentication required`, priority `high`, tags `warning,key`, `Click` set to the runbook's `main` URL, body "Hive needs a live SMS 2FA code to recover. See the runbook."
- **Auth recovered:** title `hive-app: authentication recovered`, priority `default`, tag `white_check_mark`, no `Click`, body "Hive login restored. Heating polling has resumed."
- **Runbook URL** is a constant in the notifier (repo-owned and `main`-pinned), not config. It points at a new `REAUTH_RUNBOOK.md` beside `deployments/hive-app`.
- **Notifier.** The notifier port gains a "recovered" method alongside "required". The ntfy implementation sends headers and body, and raises on HTTP errors as today.
- **ReauthAlert.** It tracks whether a "required" alert was delivered. `clear()` (called on a successful poll) sends "recovered" only if one was delivered, then resets. A failed "recovered" send is logged and swallowed, and is not retried indefinitely. Startup auth success also clears the incident. The delivered marker is in memory only, so recovery is designed around the running process: after the operator-run login the next poll (every 120s, re-reading the persisted auth state) succeeds and produces exactly one "recovered". The runbook says not to restart, because a restart before that poll forgets the marker and drops the "recovered" message (recovery itself still works).
- **Login command.** A new module run as `python -m hive_app.login`, intended for `docker exec -it hive-app ...`:
  - It loads the same `config.yml`, starts the Hive login and, when it hits the SMS challenge, prompts for the code (hidden input).
  - It completes the challenge, registers the device and writes `hive_auth_state.json` to `/config` through the existing persistence path.
  - It exits non-zero with a clear message on failure (bad code, unexpected challenge).
  - It reuses `HiveApiSource` internals, with a new method for the SMS-completing login. It does not duplicate auth logic. This reverses the earlier out-of-scope decision in the fresh-login-crash spec.
- **Config and naming.**
  - Rename `hive-config.yml.template` to `config.yml.template` under the hive-app deployment area and update all references (README, runbook, compose comments, ADR/doc mentions of `hive-config.yml`).
  - The octopus-app template keeps its name, since the two cannot share a root-level filename. Keep both templates distinct, for example by moving them next to their deployments.
  - The deployed layout is documented as `/mnt/media/pi-media/containers/<name>/config/config.yml`.
  - No live config is touched.
- **Logging.**
  - The shared `logging_config` gains a rotating file handler writing `/log/<app-logger-name>.log` alongside the existing console handler.
  - If the log directory is missing or unwritable, it falls back to console-only with a warning.
  - hive-app's compose gets `/mnt/media/pi-media/containers/hive-app/log:/log`.
  - octopus-app keeps its existing mount.
  - The UID 999 write-permission note is extended to the log directory.
  - mariadb is unchanged.
- **ADR.** Amend or supersede ADR-0018 to record: two notifications only, the topic format, and that recovery requires the login command. Update `.agent-docs/context.md` with the notification vocabulary.

## Testing Decisions

- Test external behaviour, not internals. Reuse the existing seams.
- **Notifier at the HTTP boundary** with `responses` (prior art: `test_ntfy_reauth_notifier.py`): assert the Title, Priority, Tags and Click headers and the body for both events, and that HTTP errors raise.
- **ReauthAlert, HeatingRetriever and HiveAuthenticator** with a fake notifier (prior art: `test_reauth_alert_wiring.py`, `test_heating_retrieval.py`, `test_hive_authentication.py`):
  - "Required" is sent once per incident.
  - "Recovered" is sent only after a delivered "required".
  - "Recovered" is sent once.
  - A failed send does not break polling.
  - A failed "required" delivery is retried on the next failure.
- **Login command at the HiveApiSource seam** with a mocked apyhiveapi (prior art: `test_hive_api_source_login.py`, `test_hive_auth_state_file_storage.py`): it prompts for the code, completes the challenge, persists the auth state, and fails clearly on a bad code or unexpected challenge.
- **logging_config** as a plain dict: a file handler is present with the expected path and rotation, and the fallback works when the log directory is unwritable.
- Docs, compose and template renames are not tested. Verify them with the docs/compose lint in pre-commit and `docker compose config` on the combined file.

## Out of Scope

- Any notification other than re-auth required and auth recovered (poll failures, startup or config errors, other apps).
- Sending test notifications from the codebase. The format samples were sent manually to agree the format.
- Changing live config on pi-media, or generating or storing the real GUID in the repo.
- MariaDB log handling.
- Unattended SMS 2FA. The login command is interactive by design.
- A shared config file across containers.

## Further Notes

- The format was agreed by sending sample notifications to the chosen topic. The tests' priorities and tags follow those samples, except that the "poll failing" sample was dropped.
- ntfy.sh topics are public, so the GUID in the topic name is the only secret. Rotating it means editing `ntfy.topic_url` on the Pi and resubscribing in the ntfy app.

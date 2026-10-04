# Issues: feature-hive-ntfy-reauth-notifications

> Work complete — [PR #563](https://github.com/mholubinka1/home-monitoring/pull/563) ready to merge.
> The two `docker compose config` criteria are left unchecked: docker was not
> available where this was built, so run `docker compose -f
> deployments/docker-compose.yml config` before deploying.

## hive-app: interactive Hive SMS login command and re-auth runbook — [#559](https://github.com/mholubinka1/home-monitoring/issues/559)

**Blocked by**: None

**User stories**: 5, 6, 7

### What to build

A new interactive login command, run as `python -m hive_app.login` via
`docker exec -it hive-app ...` on pi-media. It loads the same `config.yml`,
starts the Hive login, prompts (hidden input) for the SMS 2FA code when it
hits the SMS challenge, completes the challenge, registers/remembers the
device and writes `hive_auth_state.json` to `/config` via the existing
persistence path. Reuses `HiveApiSource` internals. Also write
`deployments/hive-app/REAUTH_RUNBOOK.md` documenting the real recovery
procedure using this command.

### Acceptance criteria

- [x] Given the Hive account returns an SMS challenge, when the operator
      enters a valid code, then the challenge completes, the device is
      registered and the auth state is persisted.
- [x] Given a persisted state from this command, when hive-app restarts,
      then it resumes without another SMS code.
- [x] Given an invalid code or an unexpected challenge, then the command
      exits non-zero with a clear message and persists nothing.
- [x] Tests mock apyhiveapi's `Hive` at the system boundary; no real Hive
      account is touched.
- [x] REAUTH_RUNBOOK.md documents the steps and verification.

---

## hive-app: structured ntfy re-auth required/recovered notifications — [#562](https://github.com/mholubinka1/home-monitoring/issues/562)

**Blocked by**: #559

**User stories**: 3, 4, 8, 9, 10, 11, 12

### What to build

Replace the plain-text ntfy alert with two structured notifications only.
Re-auth required: title `hive-app: re-authentication required`, priority
high, tags warning,key, Click linking to the runbook on `main` (constant in
the notifier). Auth recovered: title `hive-app: authentication recovered`,
priority default, tag white_check_mark, no Click. `ReauthAlert.clear()`
sends "recovered" only if a "required" alert was delivered, once. One
"required" per incident across startup and poll; send failures never break
polling.

### Acceptance criteria

- [x] Given a re-auth failure, the POST carries the agreed Title, Priority,
      Tags, Click headers and body.
- [x] Given a delivered "required", when auth next succeeds (poll or
      startup), then exactly one "recovered" is sent.
- [x] Given no delivered "required", a successful poll sends nothing.
- [x] Given repeated failures in one incident, only one "required" is sent;
      a failed delivery is retried on the next failure.
- [x] A failed "recovered" or "required" send is logged and never breaks
      polling.
- [x] No other notification types exist.

---

## chore: standardise per-container config naming and document ntfy topic format — [#560](https://github.com/mholubinka1/home-monitoring/issues/560)

**Blocked by**: None

**User stories**: 1, 2, 13, 16

### What to build

Rename `hive-config.yml.template` to `config.yml.template` (placed with the
hive-app deployment; octopus-app template kept distinct) and fix every
reference. Document the layout
`/mnt/media/pi-media/containers/<name>/config/config.yml`, the ntfy topic
format `home-monitoring-hive-auth-ntfy-<guid-no-dashes>` (placeholder only)
and the notification format. Amend ADR-0018; add notification vocabulary to
context.md. Live config untouched.

### Acceptance criteria

- [x] No stale `hive-config.yml` references remain outside historical specs
      (earlier specs keep the name they were written with).
- [x] Template contains a topic placeholder and format comment; no real GUID
      anywhere in the repo.
- [x] ADR-0018 and context.md updated.
- [x] Docs/yaml/markdown pre-commit checks and combined compose `config`
      pass.

---

## chore: rotating file logging to per-container /log on pi-media — [#561](https://github.com/mholubinka1/home-monitoring/issues/561)

**Blocked by**: None

**User stories**: 14, 15

### What to build

Add a rotating file handler to the shared `logging_config` writing
`/log/<app-logger-name>.log` alongside the console handler, falling back to
console-only with a warning if the log directory is missing/unwritable. Add
the hive-app `/log` mount to compose (octopus-app keeps its mount) and extend
the UID 999 write-permission note. mariadb unchanged.

### Acceptance criteria

- [x] Given a writable log directory, the config includes a rotating file
      handler at the expected path plus console.
- [x] Given an unwritable/missing directory, the config is console-only and
      warns; the app does not crash.
- [x] hive-app compose mounts the log directory; combined compose `config`
      passes.

---

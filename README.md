# Energy Monitoring

This application polls the Octopus Energy API for electricity and gas consumption and
writes it to a MariaDB database, where it can be queried and visualised using Grafana's
native MySQL data source.

Cheapest-tariff comparison and cost forecasting are planned but not yet implemented —
see `.agent-docs/specs/` for the roadmap.

## Architecture

- **`apps/octopus-app/`** — polls the Octopus API on a configurable interval and writes
  consumption readings to MariaDB (`octopus_app.data.consumption.ConsumptionRetriever` /
  `octopus_app.data.mysql.client.MariaDBClient`).
- **`apps/hive-app/`** — a second, independently deployable poller: authenticates to a
  British Gas Hive account (Cognito-SRP via the community `apyhiveapi` library, no
  official Hive API exists) and writes heating status to the same shared MariaDB
  instance every 120 seconds. See
  `.agent-docs/research/hive-api-access-approach.md` for why this auth approach was
  chosen, and `.agent-docs/specs/feature-hive-app-heating-weather.md` for the wider
  heating/weather feature this is the first slice of. Deployed via
  `deployments/hive-app/Dockerfile` and the `hive-app` service in
  `deployments/hive-app/docker-compose.yml`, configured from `deployments/hive-app/config.yml.template`.
- **`libs/common/`** — the MariaDB engine/session/schema-sync plumbing and the one
  table both apps share (`job_run`), used by both apps rather than duplicated — see
  `.agent-docs/adr/0020-shared-common-library.md`.
- **MariaDB** — the shared persistence layer for both containers. Each app's own schema
  lives in its own `data/mysql/model.py` (`apps/octopus-app/octopus_app/` and
  `apps/hive-app/hive_app/` respectively, plus `libs/common/common/mariadb/model.py`
  for `job_run`); each app's own `MariaDBClient` syncs its own tables into the live
  database automatically on startup (creating missing tables/columns only — see
  `.agent-docs/adr/0005-additive-only-schema-sync.md`). The two apps' schema syncs run
  independently against the same database — see the comment on `job_run` in
  `libs/common/common/mariadb/model.py` for the one table both currently share.
- **Grafana** (not included in this repo) — point its MySQL data source at the MariaDB
  instance to build dashboards.

## Configuration

### Application

Every container keeps its configuration and logs under its own directory on the Pi:
`/mnt/media/pi-media/containers/<name>/config/config.yml` (mounted to `/config`) and
`/mnt/media/pi-media/containers/<name>/log/` (mounted to `/log`). Each app's template
lives next to its deployment: `deployments/octopus-app/config.yml.template` and
`deployments/hive-app/config.yml.template`. Live config is never committed.

For octopus-app, create `config.yml` from `deployments/octopus-app/config.yml.template`,
providing:

- Your Octopus API key and account number, [available from your Octopus dashboard](https://octopus.energy/dashboard/new/accounts/personal-details/api-access).
- MariaDB connection details (`host`, `port`, `database`, `username`, `password`).
  **`database` must be `octopus`** — `deployments/mariadb/docker-compose.yml` hardcodes
  that name for the database MariaDB actually creates, so any other value here means
  the app can never connect to a database that exists.
- Data refresh settings: `refresh_interval_hours` (how often consumption is polled) and
  `retention_days` (how far back to backfill on every startup, and the raw-data
  retention window enforced weekly by the `prune_old_data` job, see
  [ADR-0003](.agent-docs/adr/0003-90-day-data-retention.md); no persisted watermark
  means the startup backfill re-runs in full on every restart, not just the first
  one). This is separate from the one-time 2-year `daily_consumption_summary`
  backfill that runs once on first startup (gated by `job_run` history), which needs no
  configuration.

### Docker Compose

Create `.env` from `.env.template`, providing `MARIADB_USER`/`MARIADB_PASSWORD` — the
credentials for the app's own MariaDB user. **These must match `config.yml`'s
`mariadb.username`/`password` exactly** — the two files aren't automatically kept in
sync. Docker Compose passes these values into the `mariadb` container on every start,
but MariaDB's own entrypoint only *acts* on them once — when it initializes an empty
data directory, to create that user. On a container restart against an
already-initialized data volume, MariaDB ignores them for user creation; editing `.env`
afterwards will not rotate the existing MariaDB user's password. See
[ADR-0006](.agent-docs/adr/0006-minimal-env-file-over-config-yml-only.md) for why this
one small overlap remains rather than being engineered away. `MARIADB_DATABASE` and
`MARIADB_RANDOM_ROOT_PASSWORD` are not in `.env` — they're hardcoded directly in
`deployments/mariadb/docker-compose.yml`, since neither is a secret.

### ntfy notifications (hive-app)

hive-app sends exactly two ntfy.sh notifications, both about Hive authentication (see
[ADR-0018](.agent-docs/adr/0018-ntfy-for-hive-reauth-alerting.md)); every other failure
stays on `job_run` and the dashboard.

- **Topic:** `home-monitoring-hive-auth-ntfy-<guid-no-dashes>`. ntfy.sh topics are public,
  so the GUID is the only secret: set the full `ntfy.topic_url` only in the Pi's
  `config.yml`, never in the repo. Rotating it means editing that value and resubscribing
  in the ntfy app.
- **Format:** the title is `<app>: <short event>` in lowercase; the body is one or two
  plain sentences with no timestamp (ntfy adds one); priority is `high` when action is
  needed and `default` for recovery; there is one status emoji tag; `Click` is set only
  when a useful link exists.

| Notification | Title | Priority | Tags | Click |
| --- | --- | --- | --- | --- |
| Re-auth required | `hive-app: re-authentication required` | `high` | `warning,key` | the [re-auth runbook](deployments/hive-app/REAUTH_RUNBOOK.md) on `main` |
| Auth recovered | `hive-app: authentication recovered` | `default` | `white_check_mark` | none |

"Recovered" is only sent after a "required" alert was actually delivered. Recovery needs
a live SMS 2FA code; see the runbook.

## Running

### First-time deployment

1. **Edit the bind-mount paths.** `deployments/octopus-app/docker-compose.yml`,
   `deployments/hive-app/docker-compose.yml`, and
   `deployments/mariadb/docker-compose.yml`'s `volumes:` entries
   (`/mnt/media/pi-media/containers/...`) are host-specific placeholders — change them
   to real paths on your machine before doing anything else. You need four host
   directories/files:
   - a config directory for the app (mounted to `/config`)
   - a log directory for the app (mounted to `/log`)
   - a data directory for MariaDB (mounted to `/var/lib/mysql`)
   - the repo's `data/mariadb/init.sql` copied to a path on the host (mounted read-only
     to `/docker-entrypoint-initdb.d/init.sql`)
2. **Create `config.yml`** from the app's `config.yml.template` (see Configuration above) and
   place it at the path you chose for the app's config bind mount.
3. **Create `.env`** from `.env.template` in the repository root — the directory you'll
   run `docker compose` from in the next step. Compose reads `.env` relative to the
   current working directory it's invoked from, not relative to the `-f` compose file,
   so it must live at the root even though the compose file itself is under
   `deployments/`. Fill in `MARIADB_USER`/`MARIADB_PASSWORD` to match the values you put
   in `config.yml`.
4. **Start the stack** (the combined `deployments/docker-compose.yml` `include:`s the
   three per-service files above — this is the file to actually run):

   ```bash
   docker compose -f deployments/docker-compose.yml up -d
   ```

   On first run, MariaDB initializes its (empty) data directory: it creates the
   `octopus` database (via the mounted `init.sql`) and the app's MariaDB user (via the
   `.env` credentials), then reports healthy. The `energy-monitor` container waits for
   that healthcheck before starting, connects, runs its additive schema sync (creating
   every table from scratch — see
   [ADR-0005](.agent-docs/adr/0005-additive-only-schema-sync.md)), and begins polling.
5. **Verify it worked:**

   ```bash
   docker compose -f deployments/docker-compose.yml logs -f energy-monitor
   ```

   Look for the settings-loaded and schema-sync log lines, followed by consumption
   retrieval starting. `docker compose -f deployments/docker-compose.yml ps` should
   show all three containers `Up` (`mariadb` as `healthy`).

### Subsequent deployments (updates, restarts, redeploys)

- **New image version**: `docker compose -f deployments/docker-compose.yml pull && docker compose -f deployments/docker-compose.yml up -d`
  — the `mariadb` data directory already exists, so `.env` is not re-read; the app
  container is simply replaced and re-runs its (idempotent, additive-only) schema sync
  against the existing database on startup. `watchtower` (see each per-service compose
  file's `com.centurylinklabs.watchtower.enable` label) does this automatically on its
  own schedule if it's running on the host — a manual `docker compose pull` is only
  needed for an out-of-schedule update.
- **Config changes** (`config.yml`, e.g. `refresh_interval_hours`): edit the file, then
  `docker compose -f deployments/docker-compose.yml restart energy-monitor` — no
  rebuild or pull needed.
- **Changing the MariaDB app user's password**: editing `.env` alone does **not**
  change it on an already-initialized database — you'd need to update the password in
  MariaDB directly (e.g. `ALTER USER`) and in `config.yml` together. `.env` only
  matters again if the data volume is wiped and MariaDB re-initializes from empty.
- **A brand new table/column** added by a future feature: no manual DDL step needed —
  the schema sync creates it automatically on the next `energy-monitor` startup.

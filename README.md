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
  for `job_run` and `account_postcode`); each app's own `MariaDBClient` syncs its own tables into the live
  database automatically on startup (creating missing tables/columns only — see
  `.agent-docs/adr/0005-additive-only-schema-sync.md`). The two apps' schema syncs run
  independently against the same database — see the header comment in
  `libs/common/common/mariadb/model.py` for the tables both share.
- **Grafana** (not included in this repo) — point its MySQL data source at the MariaDB
  instance to build dashboards.

## Configuration

### Application

Every container keeps its configuration and logs under its own directory on the Pi.
`<name>` is the container's name, which is also its compose service name and its
directory name: `octopus-app`, `hive-app` and `home-monitoring-db`. The apps use
`/mnt/media/pi-media/containers/<name>/config/config.yml` (mounted to `/config`) and
`/mnt/media/pi-media/containers/<name>/log/` (mounted to `/log`). The database follows
the same layout with its own contents: `home-monitoring-db/config/` holds `init.sql` and
`logging.cnf`, `home-monitoring-db/data/` is MariaDB's data directory and
`home-monitoring-db/log/` (mounted to `/var/log/mysql`) holds its error and slow-query
logs. Each app's template lives next to its deployment:
`deployments/octopus-app/config.yml.template` and
`deployments/hive-app/config.yml.template`. Live config is never committed.

For octopus-app, create `config.yml` from `deployments/octopus-app/config.yml.template`,
providing:

- Your Octopus API key and account number, [available from your Octopus dashboard](https://octopus.energy/dashboard/new/accounts/personal-details/api-access).
- MariaDB connection details (`host`, `port`, `database`, `username`, `password`).
  **`database` must be `home_monitoring`** — `deployments/mariadb/docker-compose.yml`
  hardcodes that name (`MARIADB_DATABASE`) for the database MariaDB actually creates,
  so any other value here means the app can never connect to a database that exists.
- Data refresh settings: `refresh_interval_hours` (how often consumption is polled) and
  `retention_days` (how far back to backfill on every startup, and the raw-data
  retention window enforced daily by the `prune_old_data` job, see
  [ADR-0003](.agent-docs/adr/0003-90-day-data-retention.md); no persisted watermark
  means the startup backfill re-runs in full on every restart, not just the first
  one). This is separate from the `daily_consumption_summary` backfill, which
  fetches everything the Octopus API still serves (about 2 years) at any startup where
  the summary's counted days reach back less than 6 months, and needs no configuration.

For hive-app, create `config.yml` from `deployments/hive-app/config.yml.template`. Only
the `hive` (account username and password) and `mariadb` sections are required for
heating polling; `location` and `ntfy` are optional and each
template section is commented with what uses it. Run the first Hive login, and any later
re-authentication, with `python -m hive_app.login` as described in
[the re-auth runbook](deployments/hive-app/REAUTH_RUNBOOK.md).

### Docker Compose

Create `.env` from `.env.template`, providing `MARIADB_USER`/`MARIADB_PASSWORD` — the
credentials for the app's own MariaDB user. **These must match `config.yml`'s
`mariadb.username`/`password` exactly** — the two files aren't automatically kept in
sync. Docker Compose passes these values into the `home-monitoring-db` container on every start,
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
  needed and `default` for recovery; tags are a status emoji plus, optionally, one context tag; `Click` is set only
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
   to real paths on your machine before doing anything else. Per container (named as
   described under Configuration) you need these host directories/files:
   - for each app: a config directory (mounted to `/config`) and a log directory
     (mounted to `/log`)
   - for `home-monitoring-db`: a data directory (mounted to `/var/lib/mysql`) and a log
     directory (mounted to `/var/log/mysql`, writable by the MariaDB user)
   - the repo's `data/mariadb/init.sql` copied to `home-monitoring-db/config/` (mounted
     read-only to `/docker-entrypoint-initdb.d/init.sql`)
   - the repo's `data/mariadb/logging.cnf` copied to `home-monitoring-db/config/`
     (mounted read-only into `/etc/mysql/conf.d/`; it enables the error and slow-query
     logs, so server errors go to `home-monitoring-db/log/error.log` and no longer
     appear in `docker logs`)
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
   `home_monitoring` database (via the mounted `init.sql`) and the app's MariaDB user
   (via the `.env` credentials), then reports healthy. The `octopus-app` container
   waits for that healthcheck before starting, connects, runs its additive schema
   sync (creating every table from scratch — see
   [ADR-0005](.agent-docs/adr/0005-additive-only-schema-sync.md)), and begins polling.
5. **Verify it worked:**

   ```bash
   docker compose -f deployments/docker-compose.yml logs -f octopus-app
   ```

   Look for the settings-loaded and schema-sync log lines, followed by consumption
   retrieval starting. `docker compose -f deployments/docker-compose.yml ps` should
   show all three containers `Up` (`home-monitoring-db` as `healthy`).

### Subsequent deployments (updates, restarts, redeploys)

- **New image version**: `docker compose -f deployments/docker-compose.yml pull && docker compose -f deployments/docker-compose.yml up -d`
  — the `home-monitoring-db` data directory already exists, so `.env` is not re-read; the app
  container is simply replaced and re-runs its (idempotent, additive-only) schema sync
  against the existing database on startup. `watchtower` (see each per-service compose
  file's `com.centurylinklabs.watchtower.enable` label) does this automatically on its
  own schedule if it's running on the host — a manual `docker compose pull` is only
  needed for an out-of-schedule update.
- **Config changes** (`config.yml`, e.g. `refresh_interval_hours`): edit the file, then
  `docker compose -f deployments/docker-compose.yml restart octopus-app` — no
  rebuild or pull needed.
- **Changing the MariaDB app user's password**: editing `.env` alone does **not**
  change it on an already-initialized database — you'd need to update the password in
  MariaDB directly (e.g. `ALTER USER`) and in `config.yml` together. `.env` only
  matters again if the data volume is wiped and MariaDB re-initializes from empty.
- **A brand new table/column** added by a future feature: no manual DDL step needed —
  the schema sync creates it automatically on the next `octopus-app` startup.

### Backfilling weather history

A hand-run command fills hourly weather from Open-Meteo's archive, from 2024-07-24,
for the cached Weather Location. Rows are labelled `open-meteo-archive`, live rows are
left untouched, and repeating a range is safe (rows are replaced, not duplicated).

```bash
docker exec hive-app python -m hive_app.weather_backfill --config-file /config/config.yml
```

- `--start YYYY-MM-DD` backfills from a different first day.
- hive-app must have resolved a location first; the command never geocodes.
- If a chunk fails, the message ends "Repeat from `<date>`": earlier chunks are kept, so
  re-run with `--start <date>`.
- It finishes with a completeness report of hours per local day, up to yesterday.

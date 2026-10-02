# Rename containers to octopus-app and home-monitoring-db, and align config, log and volume mounts

## Problem Statement

The containers on the Pi still carry names from before the repo became home-monitoring: `energy-monitor` (the Octopus app) and `energy-monitor-db` (MariaDB, whose compose service is called `mariadb`). hive-app already follows a clean pattern: its compose service name, `container_name` and per-container directory on pi-media are all `hive-app`, with config in `.../hive-app/config` and logs in `.../hive-app/log`. octopus-app and the database do not, so a reader cannot predict a container's directory from its name, the DB has no log directory at all, and the README says `containers/<name>/` without saying what `<name>` is.

## Solution

Every container in this repo follows one pattern: **service name = `container_name` = directory name** under `/mnt/media/pi-media/containers/<name>/`, holding `config/` (mounted at `/config`, or the service's own config location) and `log/` (mounted at `/log`, or the service's own log location). octopus-app becomes `octopus-app`, the database becomes `home-monitoring-db`, and the database also gets a mounted log directory with its error and slow-query logs enabled. A repo test pins the pattern, and a cutover runbook documents the live Pi migration, which is performed separately, after explicit confirmation, and not as part of this change.

## User Stories

1. As the operator, I want the Octopus container named `octopus-app`, so that its name matches its app, image and directory.
2. As the operator, I want the database container named `home-monitoring-db`, so that it matches the shared `home_monitoring` database it serves.
3. As the operator, I want every container's compose service name, `container_name` and pi-media directory to be the same string, so that I can find any container's files from its name alone.
4. As the operator, I want `home-monitoring-db` to have `config/`, `data/` and `log/` directories like the other containers, so that its state and logs live on pi-media.
5. As the operator, I want MariaDB's error and slow-query logs written to `home-monitoring-db/log`, so that I can inspect them without `docker logs`.
6. As the operator, I want the app containers' `depends_on` to follow the database's new service name, so that startup ordering still works.
7. As the operator, I want a test that fails if a compose file breaks the naming pattern, so that the pattern cannot drift back.
8. As the operator, I want a cutover runbook with exact commands and a rollback, so that the live rename is a supervised, reversible procedure.
9. As the operator, I want the README to say that `<name>` is the container name, so that the layout is unambiguous.
10. As a maintainer, I want ADR-0015 amended and `context.md` updated, so that the documented layout matches the repo.

## Implementation Decisions

- **Naming pattern.** Service name = `container_name` = `containers/<name>/` directory. octopus-app: `energy-monitor` → `octopus-app`. Database: service `mariadb` → `home-monitoring-db`, container `energy-monitor-db` → `home-monitoring-db`. hive-app is unchanged. The network alias `mariadb` disappears with the service rename; this is safe because both apps and Grafana reach the database by host IP (`192.168.0.10:3306`, the published port), not by container or alias name (verified against the live app configs and Grafana's datasource).
- **depends_on.** Both app services depend on `home-monitoring-db` (`condition: service_healthy`). The include-only fragments' header comments are updated to name the new service.
- **octopus-app mounts.** `.../containers/octopus-app/config:/config` and `.../containers/octopus-app/log:/log`.
- **Database mounts.**
  - `.../home-monitoring-db/data/:/var/lib/mysql/` and `.../home-monitoring-db/config/init.sql:/docker-entrypoint-initdb.d/init.sql:ro`, as today but under the new directory.
  - New: `.../home-monitoring-db/log:/var/log/mysql`.
  - New: `.../home-monitoring-db/config/logging.cnf:/etc/mysql/conf.d/logging.cnf:ro`.
- **Database logging config.** A new `data/mariadb/logging.cnf` (beside `init.sql`, which README already tells the deployer to copy to the host) sets `[mysqld]` `log_error` and `slow_query_log_file` under `/var/log/mysql`, `slow_query_log = 1` with a conservative `long_query_time`, and leaves the general query log off. Because `log_error` goes to a file, those errors no longer appear in `docker logs`; this is the accepted trade-off and is documented.
- **Layout test.** A new test under `scripts/tests` parses every `deployments/*/docker-compose.yml` and asserts, for each service: the service name equals `container_name`; every `/mnt/media/pi-media/containers/<dir>/...` host path uses a `<dir>` equal to the container name; each `depends_on` target is a service declared in some compose file; no reference to the retired names `energy-monitor`, `energy-monitor-db` or the service `mariadb` remains in `deployments/` outside the cutover runbooks. It also asserts the app services mount `/config` and `/log` and the database mounts `/var/log/mysql`.
- **Docs.**
  - README: `<name>` is the container name (= service name); the `energy-monitor` references in the run/restart/logs commands become `octopus-app`; the host-directory list gains the database `log/` directory and `logging.cnf`.
  - ADR-0015 gets a dated amendment (not a rewrite) recording the rename, the service = container = directory rule and the database log directory.
  - `context.md` updated where it names containers or the `mariadb` service.
  - `RENAME_RUNBOOK.md` (written against the old names, already executed) gets a note saying so, rather than a rewrite.
- **Cutover runbook.** A new `deployments/CUTOVER_RUNBOOK.md` (or alongside the mariadb one) documents the supervised live migration: confirm the window, stop both apps and the database, take and verify a `mariadb-dump`, `mv` the two directories in place, create `home-monitoring-db/log`, copy `logging.cnf` into `home-monitoring-db/config/`, edit the live compose in the `pi-desktop` repo (backup first, minimal anchored edits, never clobbering uncommitted user changes), remove the old stopped containers by name, bring the stack up, verify (database healthy, table count, apps running, log files appearing, `job_run` rows), and roll back by `mv` back plus restoring the compose backup.
- **Out of the repo's reach.** The Pi's live compose, `.env` and context doc live in the separate `pi-desktop` repo and are edited only during the confirmed cutover.

## Testing Decisions

- A good test asserts the pattern (names and paths agree, references resolve, retired names are gone), not the exact file text, so it survives unrelated compose edits.
- One seam: the parsed compose files, tested in `scripts/tests` (prior art: `test_rename_database.py` for deployment-adjacent tests). No new seams.
- Compose validity beyond YAML (`docker compose config`) is verified manually on the Pi, which has docker; the workstation does not.
- The live cutover is verified by its own checklist, not by automated tests.

## Out of Scope

- Executing the live cutover on the Pi (separate, after explicit confirmation).
- Changing any `pi-desktop` repo file in this change.
- MariaDB log rotation, and logging for containers outside this repo (bin-reminder, hypervolt-agile-scheduler, Grafana, InfluxDB, Telegraf).
- Renaming the Docker Hub images (already `octopus-app` and `hive-app`) or the database/schema names.
- Editing historical specs, issues and ADR bodies that mention the old names.

## Further Notes

- The directories are renamed in place with `mv` (an atomic rename on the same filesystem); no data is copied. On the Pi's media drive, `cp -p`/`sed -i` print permission warnings but work; the runbook uses plain copies for backups.
- Until the cutover runs, the repo and the Pi's live compose intentionally disagree on these names; the runbook is the bridge.

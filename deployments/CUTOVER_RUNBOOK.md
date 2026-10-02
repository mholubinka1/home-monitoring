# Pi cutover runbook: `energy-monitor` → `octopus-app`, `energy-monitor-db` → `home-monitoring-db`

Renames the live containers, their pi-media directories and the database's compose service so every container follows the [Container Naming Pattern](../.agent-docs/context.md) (service name = `container_name` = `/mnt/media/pi-media/containers/<name>/`), and gives the database a log directory with error and slow-query logs ([ADR-0015](../.agent-docs/adr/0015-pi-media-per-container-bind-mounts.md), amended). Run it in one supervised session on the Pi. Do not start until the cutover window is confirmed — both apps and the database are down for a few minutes. hive-app is not renamed but is stopped and restarted too, because it depends on the database.

Nothing here changes any application config: the apps and Grafana reach the database at `192.168.0.10:3306` (the published port), not by container or service name. **Do not rename the MariaDB user.** It happens to be called `energy-monitor` (`MARIADB_USER` in the Pi's `docker/.env`); that is a database account with grants, not a container name.

Names used below, on the Pi (`pi-desktop`):

```bash
COMPOSE=/home/pi/git/pi-desktop/docker/docker-compose.yml
ROOT=/mnt/media/pi-media/containers
```

The live compose file is in the separate `pi-desktop` repo and may hold uncommitted edits of yours. Every edit below is a minimal, anchored change made after taking a backup; never replace the file wholesale.

The media drive does not support preserving timestamps or permissions: `cp -p` and `sed -i` print "Operation not permitted" warnings (the copy or edit still happens, so check the result). `mv` of a directory on the same drive is an atomic rename and works. Use plain `cp` for backups.

## Pre-checks

1. Confirm the window with the owner of the system.

2. Confirm the logging config the database will mount is reachable on `main` (this repo's change must have merged), so step 4 cannot fail after the containers are already stopped:

   ```bash
   curl -fsS -o /dev/null -w '%{http_code}\n' https://raw.githubusercontent.com/mholubinka1/home-monitoring/main/data/mariadb/logging.cnf
   ```

   It must print `200`.

## Procedure

1. Stop the apps first, so nothing writes while the database is dumped:

   ```bash
   docker compose -f "$COMPOSE" stop energy-monitor hive-app
   ```

2. Take and verify a backup. Export the app user's password once, rather than passing it on the command line (a leading space keeps it out of history on shells with `HISTCONTROL=ignorespace`):

   ```bash
    export MYSQL_PWD=<password>
   BK=~/home_monitoring-backup-$(date +%Y%m%d%H%M%S).sql
   docker exec -e MYSQL_PWD energy-monitor-db mariadb-dump -u<user> home_monitoring > "$BK"
   echo "exit code: $?"
   grep -c '^CREATE TABLE' "$BK"
   ```

   Stop here if the exit code is not `0`. The count must print `11` (one per table — see [RENAME_RUNBOOK.md](mariadb/RENAME_RUNBOOK.md) step 5). Stop and re-run if it doesn't: do not continue to step 3 on an unverified backup. Keep this shell open (or note the value of `$BK`) for the rest of the procedure.

3. Stop the database and confirm all three containers are stopped:

   ```bash
   docker compose -f "$COMPOSE" stop mariadb
   docker ps -a --filter name=energy-monitor --filter name=hive-app --format '{{.Names}}\t{{.Status}}'
   ```

4. Rename the directories in place and prepare the database's new files (`mv` is an atomic rename; no data is copied). First check the preconditions: both sources must exist and neither destination may. `mv` onto an existing directory does not fail, it nests the source inside it, which on a rerun or partial cutover would put the live data at `home-monitoring-db/energy-monitor-db/data` while compose mounts `home-monitoring-db/data`, and MariaDB would initialise an empty database:

   ```bash
   [ -d "$ROOT/energy-monitor" ] && [ -d "$ROOT/energy-monitor-db/data" ] && [ ! -e "$ROOT/octopus-app" ] && [ ! -e "$ROOT/home-monitoring-db" ] && echo "preconditions OK" || echo "STOP: a source is missing or a destination already exists; inspect $ROOT before going on"
   ```

   Only if that printed `preconditions OK`:

   ```bash
   mv "$ROOT/energy-monitor" "$ROOT/octopus-app" && mv "$ROOT/energy-monitor-db" "$ROOT/home-monitoring-db"
   test -d "$ROOT/home-monitoring-db/data" && echo "data dir in place" || echo "STOP: data dir not at home-monitoring-db/data"
   mkdir -p "$ROOT/home-monitoring-db/log"
   curl -fsSL https://raw.githubusercontent.com/mholubinka1/home-monitoring/main/data/mariadb/logging.cnf -o "$ROOT/home-monitoring-db/config/logging.cnf"
   ls "$ROOT/octopus-app" "$ROOT/home-monitoring-db" "$ROOT/home-monitoring-db/config"
   head -3 "$ROOT/home-monitoring-db/config/logging.cnf"
   ```

   Expected: `octopus-app` has `config` and `log`; `home-monitoring-db` has `config`, `data` and `log`; its `config` has `init.sql` and `logging.cnf` (a MariaDB logging header, not an error page). The media drive is mounted world-writable (`drwxrwxrwx`, ownership ignored), so the MariaDB user can write the new `log/` directory without a `chown`; on a differently-mounted drive give UID 999 write access to it, as `deployments/hive-app/docker-compose.yml` describes for hive-app.

5. Edit the live compose file: back it up, make the anchored edits, then review the diff and validate. The service key `energy-monitor` becomes `octopus-app`, the service key `mariadb` becomes `home-monitoring-db`, both `depends_on` entries follow, the container names and mount paths change, and the database gains two mounts:

   ```bash
   BAK="$COMPOSE.bak-$(date +%Y%m%d%H%M%S)"
   cp "$COMPOSE" "$BAK"
   sed -i \
     -e 's#^  energy-monitor:$#  octopus-app:#' \
     -e 's#container_name: energy-monitor$#container_name: octopus-app#' \
     -e 's#containers/energy-monitor/#containers/octopus-app/#' \
     -e 's#^  mariadb:$#  home-monitoring-db:#' \
     -e 's#^      mariadb:$#      home-monitoring-db:#' \
     -e 's#container_name: energy-monitor-db$#container_name: home-monitoring-db#' \
     -e 's#containers/energy-monitor-db/#containers/home-monitoring-db/#' \
     "$COMPOSE"
   sed -i '/home-monitoring-db\/config\/init.sql:\/docker-entrypoint-initdb.d\/init.sql:ro/a\      - /mnt/media/pi-media/containers/home-monitoring-db/config/logging.cnf:/etc/mysql/conf.d/logging.cnf:ro\n      - /mnt/media/pi-media/containers/home-monitoring-db/log:/var/log/mysql' "$COMPOSE"
   diff "$BAK" "$COMPOSE"
   docker compose -f "$COMPOSE" config -q && echo valid
   ```

   Keep `$BAK` for the rollback. The diff must show only: the two service keys, the two `container_name` lines, the octopus-app mounts, the database's mounts plus the two new ones, and the two `depends_on` entries (octopus-app's and hive-app's). Anything else changed means stop and restore the backup. `valid` must print. (These exact commands were dry-run against a copy of the live file and produced exactly that diff.)

6. Remove the old stopped containers by name (so the old names are free and nothing is left orphaned):

   ```bash
   docker rm energy-monitor energy-monitor-db
   ```

7. Start the database first and wait for it to be healthy, then the apps. Name the services so no other container in the stack is touched. This does not change image versions: the apps start on whatever images are already pulled (watchtower keeps them current):

   ```bash
   docker compose -f "$COMPOSE" up -d home-monitoring-db
   docker inspect --format '{{.State.Health.Status}}' home-monitoring-db   # repeat until: healthy
   docker compose -f "$COMPOSE" up -d octopus-app hive-app
   ```

## Verify

- `docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'` lists `octopus-app`, `hive-app` and `home-monitoring-db` (healthy) and none of the old names.
- Table count is unchanged:

  ```bash
  docker exec -e MYSQL_PWD home-monitoring-db mariadb -u<user> home_monitoring -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='home_monitoring';"
  ```

  It must print `11`.
- The database's logs appear in `$ROOT/home-monitoring-db/log/` (`error.log`, and `slow.log` created at startup), and the effective settings are applied:

  ```bash
  docker exec -e MYSQL_PWD home-monitoring-db mariadb -u<user> -e "SHOW VARIABLES WHERE Variable_name IN ('log_error','slow_query_log','slow_query_log_file','general_log');"
  ```

  `log_error` and `slow_query_log_file` must be under `/var/log/mysql/`, `slow_query_log` `ON` and `general_log` `OFF`. Server errors now go to `error.log`, not `docker logs`.
- octopus-app and hive-app are up with no connection errors (`docker logs --tail 20 octopus-app`; hive-app's re-auth state is separate — see [REAUTH_RUNBOOK.md](hive-app/REAUTH_RUNBOOK.md)), and, on images that include file logging (this change's release or later), they write `$ROOT/octopus-app/log/octopus-monitor.log` and `$ROOT/hive-app/log/hive-monitor.log`.
- Both apps are writing again, with fresh `job_run` rows after the restart:

  ```bash
  docker exec -e MYSQL_PWD home-monitoring-db mariadb -u<user> home_monitoring -e "SELECT * FROM job_run ORDER BY id DESC LIMIT 5;"
  ```

- Grafana's MySQL data source still connects (it uses `192.168.0.10:3306`, so no change is expected).

## Rollback

The database's data is never copied or modified, only renamed, so rolling back is renaming back. If you are in a fresh shell, first re-define the variables these steps use: `COMPOSE` and `ROOT` as at the top of this runbook, and `BAK` as the compose backup made in step 5 (`ls "$COMPOSE".bak-*`; leave `BAK` unset if step 5 was never reached). Unset variables make the steps below print `STOP` or skip the compose restore. These steps work from any partial state (each one is skipped if it has nothing to do), and the data-directory check in step 3 exists because starting MariaDB against a missing data directory would silently initialise a fresh, empty database. Run them one at a time and read each output.

1. Stop whatever is running under either set of names:

   ```bash
   docker stop octopus-app home-monitoring-db hive-app energy-monitor energy-monitor-db 2>/dev/null; true
   ```

2. Put the directories back, only if they were moved:

   ```bash
   [ -d "$ROOT/octopus-app" ] && [ ! -e "$ROOT/energy-monitor" ] && mv "$ROOT/octopus-app" "$ROOT/energy-monitor"
   [ -d "$ROOT/home-monitoring-db" ] && [ ! -e "$ROOT/energy-monitor-db" ] && mv "$ROOT/home-monitoring-db" "$ROOT/energy-monitor-db"
   ```

3. Confirm the real data directory is back in place and not empty. **Do not continue if this prints `STOP`:**

   ```bash
   [ -n "$(ls -A "$ROOT/energy-monitor-db/data" 2>/dev/null)" ] && echo "data dir OK" || echo "STOP: $ROOT/energy-monitor-db/data is missing or empty"
   ```

4. If step 5 of the procedure was reached, restore the compose file and remove the new-named containers. Restoring the backup also discards any other edit made to the file since step 5, so `diff "$BAK" "$COMPOSE"` first if the file may have been touched in between:

   ```bash
   [ -f "$BAK" ] && cp "$BAK" "$COMPOSE"
   docker rm octopus-app home-monitoring-db 2>/dev/null; true
   ```

5. Start everything under the old names:

   ```bash
   docker compose -f "$COMPOSE" up -d mariadb
   docker compose -f "$COMPOSE" up -d energy-monitor hive-app
   ```

   (The extra `log/` directory and `logging.cnf` left in `energy-monitor-db/` are harmless: the restored compose does not mount them.) Then run the Verify checks against the old names.

## After the cutover

- Commit the live compose change in the `pi-desktop` repo (it also carries uncommitted edits of your own, e.g. bin-reminder, so stage deliberately).
- Update `pi-desktop`'s `.agent-docs/context.md`, whose `energy-monitor` entry still describes the old container name and image.
- Once everything has run cleanly for a while, delete the dump and the compose backups.

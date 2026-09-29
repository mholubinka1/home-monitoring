# Pi cutover runbook: `octopus` → `home_monitoring` database, image rename

Executes the deferred renames ([ADR-0022](../../.agent-docs/adr/0022-single-shared-home-monitoring-database.md), [ADR-0024](../../.agent-docs/adr/0024-docker-hub-image-name-octopus-app.md)) together, in one supervised session, against the live Pi. Do not start this until the user has explicitly confirmed the cutover window — brief downtime is expected and accepted, but it is still downtime on a live system.

Every `export MYSQL_PWD=...` below must run in the same shell session as the commands that follow it — it sets nothing outside that shell. If the session breaks or you resume this runbook later (or partway, e.g. jumping straight to step 8), re-export the credential the next step needs before running it; an unset `MYSQL_PWD` fails auth cleanly rather than silently using a stale value, but it's still a confusing detour if you don't expect it.

## Pre-checks

1. Confirm the cutover window with the user; this is not something to run unattended or opportunistically.

2. On the Pi, stop both app containers (`energy-monitor`, and hive-app's equivalent) so Schema Sync cannot run mid-rename:

   ```bash
   docker compose -f /home/pi/git/pi-desktop/docker/docker-compose.yml stop energy-monitor hive-app
   ```

   (adjust service names to match the Pi's actual compose file).

3. Export the MariaDB app user's password once as an environment variable, rather than passing `--password=<password>` on each command below — a CLI-argument password is visible to anyone with `ps` access or in shell history. `mariadb`/`mariadb-dump` read `MYSQL_PWD` for the password automatically, but there is no equivalent env var for the username — the client falls back to the OS login name if `-u`/`--user` is omitted, so the username is still passed explicitly below (it isn't sensitive). A leading space before `export` keeps the line itself out of shell history on shells with `HISTCONTROL=ignorespace` set:

   ```bash
    export MYSQL_PWD=<password>
   ```

   Separately, obtain the MariaDB **root** credential too — steps 6 and 7 below need it. The app user (`MARIADB_USER`) is scoped by grant to its own database only (`GRANT ALL PRIVILEGES ON octopus.*`), so it cannot run the migration script (`USE mysql`, `CREATE DATABASE`, and a cross-database `RENAME TABLE` all require broader privileges) or grant itself access to the new database name afterward. `deployments/mariadb/docker-compose.yml` sets `MARIADB_RANDOM_ROOT_PASSWORD: 1`, which the official image only ever reveals once, in the container's logs, at its very first startup (`docker logs energy-monitor-db 2>&1 | grep "GENERATED ROOT PASSWORD"`) — if that log line has since rotated out, or the Pi's actual (separately-synced, not-this-file) compose config manages root differently, resolve root access through however the Pi's live setup actually handles it before continuing; this runbook cannot assume a specific mechanism it hasn't observed on the real instance.

4. Take a full backup regardless of the rename script below, and verify it before continuing — a redirect alone doesn't check `mariadb-dump` actually succeeded, and a failed connection or a full disk can leave an empty or truncated file while the rest of this runbook proceeds as if the backup were good:

   ```bash
   docker exec -e MYSQL_PWD energy-monitor-db mariadb-dump -u<user> octopus > octopus-backup-$(date +%Y%m%d%H%M%S).sql
   echo "exit code: $?"
   ```

   Stop here if the exit code above is not `0`. Then confirm the file is non-empty and actually contains the expected tables, not just present:

   ```bash
   grep -c '^CREATE TABLE' octopus-backup-*.sql
   ```

   That must print `11` (one `CREATE TABLE` per table this runbook renames — see step 5's list). Stop and re-run the backup if it doesn't.

5. Record current row counts per table, to check against after the migration — an exact `COUNT(*)` per table, not `INFORMATION_SCHEMA.TABLES.TABLE_ROWS`, which for InnoDB is a statistics estimate that can drift even when nothing is actually wrong:

   ```bash
   docker exec -e MYSQL_PWD energy-monitor-db mariadb -u<user> octopus -e \
     "SELECT 'consumption', COUNT(*) FROM consumption UNION ALL
      SELECT 'agreement', COUNT(*) FROM agreement UNION ALL
      SELECT 'product', COUNT(*) FROM product UNION ALL
      SELECT 'product_rate', COUNT(*) FROM product_rate UNION ALL
      SELECT 'daily_consumption_summary', COUNT(*) FROM daily_consumption_summary UNION ALL
      SELECT 'agile_forecast', COUNT(*) FROM agile_forecast UNION ALL
      SELECT 'cost_forecast', COUNT(*) FROM cost_forecast UNION ALL
      SELECT 'heating_status', COUNT(*) FROM heating_status UNION ALL
      SELECT 'weather_observation', COUNT(*) FROM weather_observation UNION ALL
      SELECT 'weather_forecast', COUNT(*) FROM weather_forecast UNION ALL
      SELECT 'job_run', COUNT(*) FROM job_run;"
   ```

## Database rename

6. Switch `MYSQL_PWD` to the root credential from step 3, same reasoning as before — never pass it as a CLI argument either:

   ```bash
    export MYSQL_PWD=<root password>
   ```

   Copy `scripts/rename_database.sql` onto the Pi (or pipe it over SSH) and run it against the live MariaDB container **as root** (see step 3 — the app user cannot run this):

   ```bash
   docker exec -i -e MYSQL_PWD energy-monitor-db mariadb -uroot < scripts/rename_database.sql
   ```

   The script (verified by `scripts/tests/test_rename_database.py` against a throwaway container) fails loudly, before any DDL runs, if `home_monitoring` already exists or `octopus` doesn't -- a rejected run never leaves a dangling `home_monitoring` database behind, so a later, real retry is always safe. It leaves `octopus` in place, empty, as the rollback path.

7. Grant the app user access to the newly-created `home_monitoring` database, still as root — a `RENAME TABLE`/`CREATE DATABASE` does not carry over the app user's original grant on `octopus` to the new database name, so without this step every later step below (and the apps themselves, once restarted) fail with "Access denied". **Both commands below are this one step — run them together, in order; skipping the second leaves the root password set for step 8, which will then fail auth as `<user>`:**

   ```bash
   docker exec -e MYSQL_PWD energy-monitor-db mariadb -uroot -e \
     "GRANT ALL PRIVILEGES ON home_monitoring.* TO '<user>'@'%'; FLUSH PRIVILEGES;"
    export MYSQL_PWD=<password>
   ```

8. Verify row counts in `home_monitoring` match the pre-migration counts from step 5 — an exact `COUNT(*)` per table, not `INFORMATION_SCHEMA.TABLES.TABLE_ROWS`, which for InnoDB is a statistics estimate that can drift even when nothing is actually wrong:

   ```bash
   docker exec -e MYSQL_PWD energy-monitor-db mariadb -u<user> home_monitoring -e \
     "SELECT 'consumption', COUNT(*) FROM consumption UNION ALL
      SELECT 'agreement', COUNT(*) FROM agreement UNION ALL
      SELECT 'product', COUNT(*) FROM product UNION ALL
      SELECT 'product_rate', COUNT(*) FROM product_rate UNION ALL
      SELECT 'daily_consumption_summary', COUNT(*) FROM daily_consumption_summary UNION ALL
      SELECT 'agile_forecast', COUNT(*) FROM agile_forecast UNION ALL
      SELECT 'cost_forecast', COUNT(*) FROM cost_forecast UNION ALL
      SELECT 'heating_status', COUNT(*) FROM heating_status UNION ALL
      SELECT 'weather_observation', COUNT(*) FROM weather_observation UNION ALL
      SELECT 'weather_forecast', COUNT(*) FROM weather_forecast UNION ALL
      SELECT 'job_run', COUNT(*) FROM job_run;"
   ```

## Config and image cutover

**Prerequisite:** [#549](https://github.com/mholubinka1/home-monitoring/issues/549) must be deployed first. It removed the hardcoded `schema="octopus"` from every model, so the apps' tables now resolve against the database named by `mariadb.database` in `config.yml` / `hive-config.yml`. That setting is the single switch step 9 flips -- it is what retargets both the connection and every query. Do not start this section on an image built before #549.

9. Update the Pi's live `config.yml` and `hive-config.yml` (`mariadb.database`) from `octopus` to `home_monitoring`.

10. Update the Pi's own `/home/pi/git/pi-desktop/docker/docker-compose.yml`:
    - `MARIADB_DATABASE: octopus` → `home_monitoring`.
    - `image: mholubinka1/octopus-monitoring:latest` → `image: mholubinka1/octopus-app:latest`.

11. Flip `.github/workflows/ci-arm64.yml`'s `DOCKER_IMAGE` env var from `octopus-monitoring` to `octopus-app`, merge, and confirm CI has pushed at least one build under the new name before continuing (otherwise step 12 has nothing to pull).

12. Restart both containers on the Pi:

    ```bash
    docker compose -f /home/pi/git/pi-desktop/docker/docker-compose.yml up -d energy-monitor hive-app mariadb
    ```

## Verification

13. Confirm both apps are writing to `home_monitoring`, not `octopus` — check for a fresh, successful `job_run` row:

    ```bash
    docker exec -e MYSQL_PWD energy-monitor-db mariadb -u<user> home_monitoring -e \
      "SELECT * FROM job_run ORDER BY id DESC LIMIT 5;"
    ```

14. Tail both containers' logs for a normal startup cycle (Schema Sync running cleanly, no connection errors) before calling this done.

## Rollback

If anything above looks wrong before step 11 (CI/image flip) has happened:

- Revert `config.yml`/`hive-config.yml` back to `mariadb.database: octopus`.
- Revert the Pi's `docker-compose.yml` changes.
- **As root** (same as steps 6-7 — the app user cannot run any of this), reverse the rename as **one combined `RENAME TABLE` statement**, not a loop of individual ones — MariaDB treats a single multi-table `RENAME TABLE` as atomic (verified: a deliberately-failing rename left the source database completely untouched), so this either fully succeeds or leaves `home_monitoring` exactly as the forward migration left it, never partially reversed:

  ```bash
  docker exec -e MYSQL_PWD energy-monitor-db mariadb -uroot -e \
    "RENAME TABLE
       home_monitoring.consumption               TO octopus.consumption,
       home_monitoring.agreement                 TO octopus.agreement,
       home_monitoring.product                   TO octopus.product,
       home_monitoring.product_rate              TO octopus.product_rate,
       home_monitoring.daily_consumption_summary TO octopus.daily_consumption_summary,
       home_monitoring.agile_forecast            TO octopus.agile_forecast,
       home_monitoring.cost_forecast             TO octopus.cost_forecast,
       home_monitoring.heating_status            TO octopus.heating_status,
       home_monitoring.weather_observation       TO octopus.weather_observation,
       home_monitoring.weather_forecast          TO octopus.weather_forecast,
       home_monitoring.job_run                   TO octopus.job_run;"
  ```

  Before running `DROP DATABASE home_monitoring;`, confirm it actually has zero tables left — if the statement above failed, `home_monitoring` still holds some or all of the renamed tables, and dropping it would destroy them instead of the empty shell this check expects:

  ```bash
  docker exec -e MYSQL_PWD energy-monitor-db mariadb -uroot -e \
    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='home_monitoring';"
  ```

  That must print `0`. If it doesn't, stop — do not run `DROP DATABASE` — and restore from the step-4 backup instead. Once confirmed empty: `DROP DATABASE home_monitoring;` and `REVOKE ALL PRIVILEGES ON home_monitoring.* FROM '<user>'@'%';` (the grant from step 7).
- Restart the containers against the restored `octopus` state.

If the CI/image flip (step 11) has already merged, the image name can stay flipped independently of the database rollback — they are not coupled once step 11 has run; only revert `DOCKER_IMAGE` too if the new image itself is the problem.

## Later, separate step (not part of this runbook)

Once `home_monitoring` has been running successfully for a while, drop the now-empty `octopus` database and the old `octopus-monitoring` image tag on Docker Hub. Track this as its own explicitly-confirmed follow-on — never as an automatic step of this runbook.

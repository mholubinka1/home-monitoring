# Pi cutover runbook: `octopus` → `home_monitoring` database, image rename

Executes the deferred renames ([ADR-0022](../../.agent-docs/adr/0022-single-shared-home-monitoring-database.md), [ADR-0024](../../.agent-docs/adr/0024-docker-hub-image-name-octopus-app.md)) together, in one supervised session, against the live Pi. Do not start this until the user has explicitly confirmed the cutover window — brief downtime is expected and accepted, but it is still downtime on a live system.

## Pre-checks

1. Confirm the cutover window with the user; this is not something to run unattended or opportunistically.

2. On the Pi, stop both app containers (`energy-monitor`, and hive-app's equivalent) so Schema Sync cannot run mid-rename:

   ```bash
   docker compose -f /home/pi/git/pi-desktop/docker/docker-compose.yml stop energy-monitor hive-app
   ```

   (adjust service names to match the Pi's actual compose file).

3. Export the MariaDB credentials once as environment variables, rather than passing `--password=<password>` on each command below — a CLI-argument password is visible to anyone with `ps` access or in shell history. `mariadb`/`mariadb-dump` read `MYSQL_USER`/`MYSQL_PWD` automatically. A leading space before `export` keeps the line itself out of shell history on shells with `HISTCONTROL=ignorespace` set:

   ```bash
    export MYSQL_USER=<user> MYSQL_PWD=<password>
   ```

4. Take a full backup regardless of the rename script below:

   ```bash
   docker exec -e MYSQL_PWD -e MYSQL_USER energy-monitor-db mariadb-dump octopus > octopus-backup-$(date +%Y%m%d%H%M%S).sql
   ```

5. Record current row counts per table, to check against after the migration:

   ```bash
   docker exec -e MYSQL_PWD -e MYSQL_USER energy-monitor-db mariadb octopus -e \
     "SELECT TABLE_NAME, TABLE_ROWS FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='octopus';"
   ```

## Database rename

6. Copy `scripts/rename_database.sql` onto the Pi (or pipe it over SSH) and run it against the live MariaDB container:

   ```bash
   docker exec -i -e MYSQL_PWD -e MYSQL_USER energy-monitor-db mariadb < scripts/rename_database.sql
   ```

   The script (verified by `scripts/tests/test_rename_database.py` against a throwaway container) fails loudly, before any DDL runs, if `home_monitoring` already exists or `octopus` doesn't -- a rejected run never leaves a dangling `home_monitoring` database behind, so a later, real retry is always safe. It leaves `octopus` in place, empty, as the rollback path.

7. Verify row counts in `home_monitoring` match the pre-migration counts from step 5:

   ```bash
   docker exec -e MYSQL_PWD -e MYSQL_USER energy-monitor-db mariadb home_monitoring -e \
     "SELECT TABLE_NAME, TABLE_ROWS FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='home_monitoring';"
   ```

## Config and image cutover

8. Update the Pi's live `config.yml` and `hive-config.yml` (`mariadb.database`) from `octopus` to `home_monitoring`.

9. Update the Pi's own `/home/pi/git/pi-desktop/docker/docker-compose.yml`:
   - `MARIADB_DATABASE: octopus` → `home_monitoring`.
   - `image: mholubinka1/octopus-monitoring:latest` → `image: mholubinka1/octopus-app:latest`.

10. Flip `.github/workflows/ci-arm64.yml`'s `DOCKER_IMAGE` env var from `octopus-monitoring` to `octopus-app`, merge, and confirm CI has pushed at least one build under the new name before continuing (otherwise step 11 has nothing to pull).

11. Restart both containers on the Pi:

    ```bash
    docker compose -f /home/pi/git/pi-desktop/docker/docker-compose.yml up -d energy-monitor hive-app mariadb
    ```

## Verification

12. Confirm both apps are writing to `home_monitoring`, not `octopus` — check for a fresh, successful `job_run` row:

    ```bash
    docker exec -e MYSQL_PWD -e MYSQL_USER energy-monitor-db mariadb home_monitoring -e \
      "SELECT * FROM job_run ORDER BY id DESC LIMIT 5;"
    ```

13. Tail both containers' logs for a normal startup cycle (Schema Sync running cleanly, no connection errors) before calling this done.

## Rollback

If anything above looks wrong before step 10 (CI/image flip) has happened:

- Revert `config.yml`/`hive-config.yml` back to `mariadb.database: octopus`.
- Revert the Pi's `docker-compose.yml` changes.
- `RENAME TABLE home_monitoring.<t> TO octopus.<t>` for each of the nine tables (the reverse of `scripts/rename_database.sql`), then `DROP DATABASE home_monitoring;`.
- Restart the containers against the restored `octopus` state.

If the CI/image flip (step 10) has already merged, the image name can stay flipped independently of the database rollback — they are not coupled once step 10 has run; only revert `DOCKER_IMAGE` too if the new image itself is the problem.

## Later, separate step (not part of this runbook)

Once `home_monitoring` has been running successfully for a while, drop the now-empty `octopus` database and the old `octopus-monitoring` image tag on Docker Hub. Track this as its own explicitly-confirmed follow-on — never as an automatic step of this runbook.

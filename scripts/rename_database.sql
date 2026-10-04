-- Rename the `octopus` database to `home_monitoring` (ADR-0022).
--
-- Run this once, during the confirmed Pi cutover window described in
-- the retired RENAME_RUNBOOK.md (`git show 9522fda:deployments/mariadb/RENAME_RUNBOOK.md`),
-- with both app containers stopped.
-- Moves every table Schema Sync currently owns via RENAME TABLE, which
-- MariaDB performs as an atomic metadata operation (no data copy). Leaves
-- `octopus` in place, empty, as the rollback path -- this script never
-- drops it.
--
-- Requires MariaDB root (USE mysql, CREATE DATABASE, and a cross-database
-- RENAME TABLE all need privileges beyond an app user's own
-- GRANT ... ON octopus.* grant) -- the retired RENAME_RUNBOOK.md (see git history)
-- had the full procedure, including granting the app user access to the
-- new database name afterward (a rename does not carry a grant over).
--
-- Usage: export MYSQL_PWD=<root password>; mariadb --user=root < scripts/rename_database.sql
--
-- Note: CREATE DATABASE/PROCEDURE and RENAME TABLE are DDL, which MariaDB
-- commits implicitly and cannot roll back as a unit -- there is no
-- transactional wrapper that would make this atomic across all statements.
-- The guard below exists precisely because of that: if `octopus` were
-- missing, CREATE DATABASE would still succeed before RENAME TABLE failed,
-- leaving a dangling empty `home_monitoring` that then falsely blocks any
-- later, real retry with "already exists" -- so the precondition is
-- checked, and rejected, before any DDL that could leave that partial
-- state runs at all.
--
-- Tested by scripts/tests/test_rename_database.py against a throwaway
-- MariaDB container.

-- Defined in `mysql` since no database is selected by default on a fresh
-- connection. Dropped first (IF EXISTS) in case a prior run aborted before
-- its own cleanup ran -- a guard failure must not permanently break future
-- runs.
USE mysql;

DROP PROCEDURE IF EXISTS rename_database_guard;

DELIMITER //
CREATE PROCEDURE rename_database_guard()
BEGIN
    IF (SELECT COUNT(*) FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = 'octopus') = 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Source database `octopus` does not exist -- nothing to migrate.';
    END IF;
    IF (SELECT COUNT(*) FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = 'home_monitoring') > 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Target database `home_monitoring` already exists -- already migrated, or name collision. Aborting.';
    END IF;
    -- `octopus` existing isn't enough -- every table below must exist too, or
    -- CREATE DATABASE would still succeed before RENAME TABLE hit the missing
    -- one, leaving the same dangling home_monitoring database this guard
    -- otherwise prevents. Checked as one count against the full expected set,
    -- not per-table, so a single query names every table missing at once via
    -- the row count gap.
    IF (SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = 'octopus'
          AND TABLE_NAME IN (
              'consumption', 'agreement', 'product', 'product_rate',
              'daily_consumption_summary', 'agile_forecast', 'cost_forecast',
              'heating_status', 'weather_observation', 'weather_forecast', 'job_run'
          )) <> 11 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'One or more of the eleven expected tables (consumption, agreement, product, product_rate, daily_consumption_summary, agile_forecast, cost_forecast, heating_status, weather_observation, weather_forecast, job_run) is missing from `octopus` -- aborting before any DDL runs.';
    END IF;
END //
DELIMITER ;

CALL rename_database_guard();
DROP PROCEDURE rename_database_guard;

CREATE DATABASE home_monitoring;

RENAME TABLE
    octopus.consumption               TO home_monitoring.consumption,
    octopus.agreement                 TO home_monitoring.agreement,
    octopus.product                   TO home_monitoring.product,
    octopus.product_rate              TO home_monitoring.product_rate,
    octopus.daily_consumption_summary TO home_monitoring.daily_consumption_summary,
    octopus.agile_forecast            TO home_monitoring.agile_forecast,
    octopus.cost_forecast             TO home_monitoring.cost_forecast,
    octopus.heating_status            TO home_monitoring.heating_status,
    octopus.weather_observation       TO home_monitoring.weather_observation,
    octopus.weather_forecast          TO home_monitoring.weather_forecast,
    octopus.job_run                   TO home_monitoring.job_run;

-- `octopus` is intentionally left in place, now empty -- do not DROP it here.
-- Dropping it is a later, separately-confirmed step, once home_monitoring
-- has been running successfully for a while (tracked in issue #589).

-- Rename the `octopus` database to `home_monitoring` (ADR-0022).
--
-- Run this once, during the confirmed Pi cutover window described in
-- deployments/mariadb/RENAME_RUNBOOK.md, with both app containers stopped.
-- Moves every table Schema Sync currently owns via RENAME TABLE, which
-- MariaDB performs as an atomic metadata operation (no data copy). Leaves
-- `octopus` in place, empty, as the rollback path -- this script never
-- drops it.
--
-- Usage: mariadb --user=<user> --password=<password> < scripts/rename_database.sql
--
-- Tested by scripts/tests/test_rename_database.py against a throwaway
-- MariaDB container.

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
    octopus.job_run                   TO home_monitoring.job_run;

-- `octopus` is intentionally left in place, now empty -- do not DROP it here.
-- Dropping it is a later, separately-confirmed step, once home_monitoring
-- has been running successfully for a while (see the runbook).

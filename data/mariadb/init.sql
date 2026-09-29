-- MariaDB init script
--
-- Table creation is handled by each app's MariaDBClient schema sync on startup
-- (see apps/octopus-app/octopus_app/data/mysql/model.py,
-- apps/hive-app/hive_app/data/mysql/model.py, libs/common/common/mariadb/model.py,
-- and .agent-docs/adr/0005-additive-only-schema-sync.md).
-- This file only needs to guarantee the database itself exists.

CREATE DATABASE IF NOT EXISTS home_monitoring;

# Issues: chore-rename-containers-octopus-app-home-monitoring-db

> Work complete — [PR #567](https://github.com/mholubinka1/home-monitoring/pull/567) ready to merge.
> The live Pi cutover is deliberately not part of this change; it is run
> separately from `deployments/CUTOVER_RUNBOOK.md` after explicit confirmation.

## chore: rename containers to octopus-app and home-monitoring-db with a layout test — [#564](https://github.com/mholubinka1/home-monitoring/issues/564)

**Blocked by**: None

**User stories**: 1, 2, 3, 6, 7

### What to build

Rename the containers in the compose files and pin the naming pattern with a
test. octopus-app: service and `container_name` `energy-monitor` →
`octopus-app`, mounts `.../containers/octopus-app/{config,log}`. Database:
service `mariadb` → `home-monitoring-db`, `container_name`
`energy-monitor-db` → `home-monitoring-db`, data and init.sql mounts under
`.../containers/home-monitoring-db/`. Both apps' `depends_on` follow the new
service name; fragment header comments updated. A new test under
`scripts/tests` parses every `deployments/*/docker-compose.yml` and asserts
the naming pattern, mount layout, `depends_on` resolution and absence of the
retired names.

### Acceptance criteria

- [x] Given the compose files, every service name equals its container_name
      and its pi-media directory name.
- [x] Given octopus-app and hive-app, each mounts `/config` and `/log` from
      its own `containers/<name>/` directory.
- [x] Given the app services, `depends_on` targets `home-monitoring-db`,
      which is a declared service.
- [x] Given `deployments/`, no retired name remains outside the runbooks.
- [x] The combined `deployments/docker-compose.yml` validates with
      `docker compose config`.

---

## chore: MariaDB error and slow-query logs on home-monitoring-db/log — [#565](https://github.com/mholubinka1/home-monitoring/issues/565)

**Blocked by**: #564

**User stories**: 4, 5

### What to build

Give `home-monitoring-db` a log directory and enable MariaDB's error and
slow-query logs: a new `data/mariadb/logging.cnf` beside `init.sql`, mounted
read-only into `/etc/mysql/conf.d/`, with the log directory mounted at
`/var/log/mysql`. The general query log stays off. The layout test is
extended to cover the mounts and the cnf contents. The `docker logs` trade-off
(errors go to the file) is documented.

### Acceptance criteria

- [x] Given the database service, `/var/log/mysql` is mounted from
      `containers/home-monitoring-db/log`.
- [x] Given `logging.cnf`, error and slow-query logs are enabled with files
      under `/var/log/mysql`, and the general query log is not enabled.
- [x] The logging config is mounted read-only into `/etc/mysql/conf.d/`.
- [x] The `docker logs` trade-off is documented where the config is
      introduced.

---

## chore: rename docs, ADR-0015 amendment and live cutover runbook — [#566](https://github.com/mholubinka1/home-monitoring/issues/566)

**Blocked by**: #564, #565

**User stories**: 8, 9, 10

### What to build

README (`<name>` is the container name; new names throughout; database
`log/` and `logging.cnf` in the host-directory list), a dated ADR-0015
amendment, `context.md` updates, a note on `RENAME_RUNBOOK.md` that it was
written against the old names, and a new `deployments/CUTOVER_RUNBOOK.md`
for the supervised live migration (stop, backup, `mv`, compose edit, remove
old containers, bring up, verify, roll back).

### Acceptance criteria

- [x] README states that `<name>` is the container name and uses the new
      names throughout.
- [x] ADR-0015 has a dated amendment; `context.md` and RENAME_RUNBOOK are
      consistent with the rename.
- [x] CUTOVER_RUNBOOK.md has exact commands, a verification checklist and a
      rollback.
- [x] Docs/markdown pre-commit checks pass.

---

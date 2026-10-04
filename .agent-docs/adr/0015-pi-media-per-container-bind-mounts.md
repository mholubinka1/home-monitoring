---
status: accepted
---

# Move host bind mounts to a per-container `pi-media/containers/<name>/` root

The reference `docker-compose.yml` previously placed the stack's host bind mounts under a
shared-by-type tree on the media drive: `/mnt/media/pi-media/monitoring/config/energy-monitor`,
`/mnt/media/pi-media/monitoring/log/energy-monitor`, `/mnt/media/pi-media/monitoring/data/mysql`,
`/mnt/media/pi-media/monitoring/config/mysql/init.sql`. That `monitoring/` tree is shared on
the deployment host with unrelated stacks (Grafana, InfluxDB, Telegraf), so this app's
directories were interleaved with theirs and there was no single path that was "everything
this stack writes".

Both services now mount under one per-container root instead:

```text
/mnt/media/pi-media/containers/energy-monitor/{config,log}
/mnt/media/pi-media/containers/energy-monitor-db/{config,data}
```

`energy-monitor`'s `config.yml` and its `/log` output live under `containers/energy-monitor/`;
MariaDB's data directory and its `init.sql` live under `containers/energy-monitor-db/`, keyed
on each service's `container_name`. This matches the convention the deployment host already
uses for its other single-container stacks (`containers/bin-reminder/`,
`containers/hypervolt-agile-scheduler/`).

Container-internal mount points are unchanged — `energy-monitor` still mounts `/config` and
`/log`, MariaDB still mounts `/var/lib/mysql` and `/docker-entrypoint-initdb.d/init.sql` — so
no application config (`config.yml`, the app's log path) needed editing. The host log leaf is
named `log` to match the sibling-stack convention; here the container mount is already `/log`
so there is no divergence, but the sibling stacks name it `log` even where the container
mount is `/logs`.

## Considered Options

- **Keep the `monitoring/` shared-by-type layout.** Rejected: it offers no single path for
  "this stack's state", and the host is standardising every other single-purpose stack on a
  per-container root.
- **Migrate `energy-monitor` only, leave MariaDB on `monitoring/`.** Rejected: leaves the
  stack half-migrated and the reference compose incoherent for a fresh deployer who has no
  `monitoring/` tree.

## Consequences

- The host paths in `docker-compose.yml` remain host-specific placeholders that a personal
  deployment is expected to edit — see the README's first-time-deployment step. This ADR
  records the default layout, not a requirement.
- Only this stack's directories move. The other stacks sharing the host's `monitoring/` tree
  (Grafana, InfluxDB, Telegraf) are deliberately not migrated by this change.
- The committed deploy compose (in the `pi-desktop` repo) and any uncommitted workstation
  copy of it will diverge on these paths until reconciled by hand. The cutover on the host
  moves the live MariaDB data directory and copies `config.yml` / `init.sql` into the new
  layout; it does not depend on this repo's PR merging first.
- Historical specs and ADRs that mention the old `monitoring/...` paths (e.g.
  `specs/chore-operational-hygiene.md`, `specs/feature-grafana-dashboard.md`) are dated
  records and are left unchanged.

## Amendment (2026-10-02): containers renamed, and the rule made explicit

The directory layout above was keyed on each service's `container_name`, but the names themselves were inherited from before the repo became home-monitoring: `energy-monitor`, `energy-monitor-db` and a service called `mariadb`. They are now `octopus-app` and `home-monitoring-db`, matching hive-app, and the layout rule is stated explicitly: **a container's compose service name, its `container_name` and its directory under `/mnt/media/pi-media/containers/` are the same string.** A test (`scripts/tests/test_deployment_layout.py`) pins this.

```text
/mnt/media/pi-media/containers/octopus-app/{config,log}
/mnt/media/pi-media/containers/hive-app/{config,log}
/mnt/media/pi-media/containers/home-monitoring-db/{config,data,log}
```

The database also gets a `log/` directory (mounted at `/var/log/mysql`) with its error and slow-query logs enabled through a mounted `logging.cnf`. The apps and Grafana reach the database by host IP and the published port, not by container or service name, so renaming it needed no application config change. The live Pi is migrated by the supervised procedure in `deployments/CUTOVER_RUNBOOK.md` (since resolved: as of 2026-10-04 the Pi's running containers are `octopus-app`, `hive-app` and `home-monitoring-db`). The text above this amendment is kept as originally written, so it still names the old containers.

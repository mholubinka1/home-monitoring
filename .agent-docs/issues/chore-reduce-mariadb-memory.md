# Issues: chore/reduce-mariadb-memory

## Apps reconnect cleanly after a MariaDB restart

**Blocked by**: None

**User stories**: 5

### What to build

Make the shared database client replace a dead pooled connection transparently, so
that when MariaDB restarts (memory cap, Watchtower update or manual recreate) both
hive-app and octopus-app's next database operation succeeds instead of failing once
and waiting out the retry backoff. The change is in the shared library, so both apps
get it; no public interface changes.

### Acceptance criteria

- [ ] Given a client that has already completed an operation against a real MariaDB container, when the server kills all of that client's connections, then the client's next operation succeeds without raising
- [ ] The new test fails against the engine as it is today and passes with the change
- [ ] The test skips cleanly when Docker is unavailable, like the existing real-MariaDB tests
- [ ] Existing library and app tests still pass

---

## Configure the MariaDB container for low, capped memory

**Blocked by**: None (the live apply waits for #735 to be deployed, so the database restart is already clean)

**User stories**: 1, 2, 3, 4, 6, 7, 8

### What to build

Add the memory configuration for `home-monitoring-db` to the repo: the glibc arena
limit, a new `memory.cnf` that shrinks the unused Aria and key buffers, and a hard
memory cap with no swap, wired into the reference compose file and covered by the
deployment-layout tests. Each setting carries a short comment explaining why it
exists, so it isn't mistaken for an arbitrary tweak and reverted.

The deployment of this change is part of the slice. After merge, with the user's
go-ahead, apply it to the live Pi over SSH. The Pi's kernel memory cgroup is
disabled (confirmed 2026-11-04), so Docker would silently ignore the cap: first back
up `/boot/firmware/cmdline.txt`, add `cgroup_enable=memory` to its single line and
reboot, which takes down every service on the Pi, so get the user's go-ahead for the
reboot. Then copy `memory.cnf` to the host config directory, edit the live
`pi-desktop` compose file to match the reference copy, and recreate
`home-monitoring-db`.

### Acceptance criteria

- [x] The reference compose file sets `MALLOC_ARENA_MAX` to 2 on the database service
- [x] The database service mounts `memory.cnf` read-only at `/etc/mysql/conf.d/memory.cnf` from `containers/home-monitoring-db/config/`
- [x] `memory.cnf` sets `aria_pagecache_buffer_size` and `key_buffer_size` to 8M in `[mysqld]`, with a header comment explaining the values and the copy-to-host step
- [x] The database service sets `mem_limit` to 512m and `memswap_limit` equal to it (no swap)
- [x] Deployment-layout tests assert each of the above and fail if any is removed
- [x] The existing `logging.cnf` tests and all other deployment-layout tests still pass
- [ ] Every Docker CLI timeout in the `libs/common` and `scripts` test fixtures is one named 60 s constant, so a loaded Pi no longer errors the real-database tests and drops coverage below the gate
- [ ] Live: the Pi's memory cgroup is enabled: `cgroup_enable=memory` added to `/boot/firmware/cmdline.txt`, the Pi rebooted, no `No memory limit support` warning in `docker info`, and every container healthy again
- [ ] Live: the apps run an image that includes the reconnect fix (#735) before the database is recreated
- [ ] Live: `memory.cnf` is on the host and the live `pi-desktop` compose file matches the reference copy in the repo
- [ ] Live: `home-monitoring-db` is recreated with the new settings

---

## Verify memory stays under 300 MB on the Pi

**Blocked by**: #735, #736

**User stories**: 1, 2, 7, 8

### What to build

After the config is deployed (#736), verify it took effect and that memory stays down.
Check the new settings are live straight after the recreate, confirm the kernel
enforces the cap, and confirm the apps carried on working. Then, seven days later,
check `mariadbd` RSS.

### Acceptance criteria

- [ ] `@@aria_pagecache_buffer_size` and `@@key_buffer_size` report 8 MB on the live database
- [ ] `MALLOC_ARENA_MAX=2` is in the live container's environment
- [ ] `docker inspect` shows the 512 MB memory and memory+swap limits
- [ ] `docker stats --no-stream home-monitoring-db` shows a limit of `512MiB`, not `0B`
- [ ] Both apps' next scheduled jobs after the recreate record successful job runs
- [ ] Seven days after the deployment, `mariadbd` RSS is under 300 MB
- [ ] The existing slow query log has been scanned for new entries (informational)

---

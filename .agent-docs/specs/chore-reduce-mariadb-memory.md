# Reduce home-monitoring-db memory on the Pi

## Problem Statement

The Raspberry Pi (`pi-desktop`, 3.8 GB RAM) that hosts the home-monitoring stack is
under memory pressure: 46 MB free, 626 MB available, and its 199 MB swap file
completely full. The single largest consumer is the `home-monitoring-db` MariaDB
container, at 1.2 GB resident (about 32% of the Pi's RAM) after 2.3 days of uptime,
even though the `home_monitoring` database holds only 22 MB of data.

Investigation on the live Pi (2026-11-04, MariaDB 13.0.2 on aarch64,
`mariadb:latest`) found two causes:

1. **glibc allocator arenas, about 1 GB.** `mariadbd` uses the system glibc
   allocator (`@@version_malloc_library = system`). Its memory map holds about
   18 anonymous regions of about 64 MB each, nearly all resident. 64 MB is glibc's
   per-thread arena size on 64-bit platforms. glibc creates up to 8 arenas per core
   and keeps freed memory inside them instead of returning it to the OS, so the
   footprint ratchets up with uptime. MariaDB's own accounting (`Memory_used`,
   about 480 MB) doesn't see this memory at all.
2. **Oversized default buffers.** About 470 MB is reserved at startup
   (`Memory_used_initial`), sized for a much larger server. Two of these buffers
   are effectively unused here: the 128 MB Aria pagecache (used only by internal
   temp tables) and the 128 MB MyISAM key buffer (no table is MyISAM). The 128 MB
   InnoDB buffer pool is mostly empty (6,943 of 8,112 pages free) but stays as-is.

There is also no memory limit on the container, so nothing stops MariaDB's growth
from starving the other services on the Pi (Plex, Grafana, InfluxDB, the apps).

This is already causing failures. On 2026-11-04 the Pi was overloaded (load average
32.6 on 4 cores, 52 MB free, swap 100% full), and other services started failing:

- hive-app lost Hive data for 33 minutes because DNS lookups for
  `sso.hivehome.com` kept timing out (`[Errno -3] Try again`).
- One hive-app database connection failed with `SSL: WRONG_VERSION_NUMBER`.
- SSH to the Pi timed out during the banner exchange.

## Solution

Bring MariaDB's steady-state memory under 300 MB and keep it there:

- Limit glibc to 2 shared arenas, so freed memory is reused instead of parked in
  per-thread pools.
- Shrink the two unused startup buffers to 8 MB each.
- Put a hard 512 MB memory cap (with no swap) on the container as a safety net. If
  MariaDB ever exceeds it, the kernel kills it and Docker restarts it within
  seconds. The rest of the Pi is protected, and a brief database outage is
  acceptable.
- Make both apps reconnect cleanly after any MariaDB restart, so that restart
  safety net doesn't cause failed job attempts.

Memory takes priority over query speed. Some dashboard slowdown is acceptable,
though none is expected, since the InnoDB buffer pool is unchanged.

## User Stories

1. As the operator of the Pi, I want MariaDB to use under 300 MB of RAM after days of
   uptime, so that the other services on the Pi have memory to run and the swap file
   stops being exhausted.
2. As the operator, I want MariaDB's memory to stay flat over time instead of creeping
   up with uptime, so that I don't have to restart the database periodically to
   reclaim memory.
3. As the operator, I want a hard ceiling on MariaDB's memory, so that a runaway
   query or leak in the database can never starve Plex, Grafana or the apps.
4. As the operator, I want MariaDB never to be pushed into swap by its own memory
   limit, so that a database under pressure fails fast and restarts instead of
   slowing to a crawl.
5. As the operator, I want hive-app and octopus-app to carry on working after
   MariaDB restarts (whether from the memory cap, a Watchtower image update or a
   manual recreate), without a failed attempt or a 60-second retry wait, so that a
   restart doesn't leave gaps or noise in job runs.
6. As a future reader of the deployment config (human or agent), I want each memory
   setting to explain why it's there, so that `MALLOC_ARENA_MAX` or an 8 MB buffer
   isn't mistaken for an arbitrary tweak and reverted.
7. As the operator, I want the reference compose file in this repo to match what's
   deployed on the Pi, so that it stays a trustworthy record of the live MariaDB
   configuration.
8. As the operator, I want the memory settings to survive Watchtower pulling a new
   `mariadb:latest` image, so that an automatic update doesn't quietly undo the fix.

## Implementation Decisions

- **Allocator: `MALLOC_ARENA_MAX=2`** as an environment variable on the
  `home-monitoring-db` service. This is a plain glibc feature with no
  image-specific paths, so it survives Watchtower image updates. The trade-off is
  slightly more allocator lock contention, which is irrelevant at about 10
  connections.
  - *Rejected:* jemalloc via `LD_PRELOAD`. The image ships it and MariaDB prefers
    it, but it hardcodes an architecture-specific library path
    (`/usr/lib/aarch64-linux-gnu/libjemalloc.so.2`) inside an auto-updated image.
    If a future image moved or dropped it, the loader would skip it with only a
    warning and silently fall back to glibc. Revisit only if the 7-day check
    misses the target.
- **Buffers: a new MariaDB option file, `memory.cnf`**, kept next to `logging.cnf`
  in the repo's `data/mariadb/` directory. It follows the same pattern: copied to
  `containers/home-monitoring-db/config/` on the host and mounted read-only into
  `/etc/mysql/conf.d/`. It's a separate file from `logging.cnf` so each file has
  one concern. Its `[mysqld]` section sets exactly:
  - `aria_pagecache_buffer_size = 8M` (default 128M)
  - `key_buffer_size = 8M` (default 128M)

  Everything else stays at MariaDB defaults, including
  `innodb_buffer_pool_size` (128M), `innodb_log_buffer_size`, `max_connections`
  and `thread_cache_size`. This is the "light" trim: the lowest-risk option that
  still meets the target once the arena fix is in. The file's header comment
  explains each value and the copy-to-host step, matching `logging.cnf`'s
  header.
- **Hard cap: `mem_limit: 512m` and `memswap_limit: 512m`** on the
  `home-monitoring-db` service. Setting them equal means the container gets no
  swap at all. 512 MB leaves about 200 MB of headroom over the 300 MB target, so
  the cap only fires on a genuine runaway. Exceeding it means an OOM kill and a
  restart under the existing `restart: unless-stopped` policy.
- **Clean reconnect: `pool_pre_ping=True`** on the shared SQLAlchemy engine built by
  `SessionBuilder` in `libs/common`. On each checkout, the pool pings the
  connection and transparently replaces it if it's dead. Both apps get this from
  the shared library, and nothing in the client's public interface changes.
  - Today, the first operation after a restart fails on a stale pooled
    connection. Jobs wrapped in `retry_with_exponential_backoff` recover after a
    60-second wait, and unwrapped jobs fail until their next scheduled run.
  - Cost: one lightweight round-trip per connection checkout.
- **Prerequisite: kernel memory cgroup on the Pi.** The 512 MB cap only works if the
  Pi's kernel has the memory cgroup enabled. Raspberry Pi OS ships with it disabled
  unless `cgroup_enable=memory` is in `/boot/firmware/cmdline.txt`. When it is
  disabled, Docker accepts a `mem_limit` without error and then ignores it, so the
  safety net would silently do nothing.
  - Evidence (2026-11-04): `docker stats` on `pi-desktop` reports `0B / 0B` memory
    for every container, the usual symptom of the memory cgroup being disabled.
    Confirmed the same day: `docker info` warns `No memory limit support` and
    `No swap limit support`, and `/boot/firmware/cmdline.txt` has no
    `cgroup_enable=memory`. So the reboot is required for stories 3 and 4.
  - Check first: `docker info | grep -i "memory limit"` on the Pi. A warning such
    as `WARNING: No memory limit support` means the memory cgroup is disabled.
  - If disabled: add `cgroup_enable=memory` to the single line in
    `/boot/firmware/cmdline.txt` and reboot the Pi. Plan the reboot, because it
    takes down every service on the Pi, not only MariaDB.
  - Verify after deploy: `docker stats --no-stream home-monitoring-db` shows a
    limit of `512MiB`, not `0B`.
  - `MALLOC_ARENA_MAX=2` and the 8 MB buffers don't depend on this; they reduce
    memory whether or not the cap works. Only User Story 3 (hard ceiling) and User
    Story 4 (no swap) need it.
- **Reference compose file.** `deployments/mariadb/docker-compose.yml` gains the
  environment variable, the `memory.cnf` read-only mount and both memory limits.
  It stays a hand-synced reference copy. The live definition remains in the
  `pi-desktop` stack (`/home/pi/git/pi-desktop/docker/docker-compose.yml`), which
  this repo doesn't own.
- **Rollout (after merge, by Claude over SSH, with the user's go-ahead at that
  point):**
  0. Enable the memory cgroup (prerequisite above, confirmed disabled): back up
     `/boot/firmware/cmdline.txt`, add `cgroup_enable=memory` to its single line
     and reboot the Pi. This is part of the deployment of the config change, not
     a separate effort. The reboot takes down every service on the Pi, so get the
     user's go-ahead before doing it. Confirm `docker info` no longer warns about
     memory limit support and every container comes back healthy before the steps
     below.
  1. Confirm both app containers are running an image that includes
     `pool_pre_ping`, either from Watchtower or by pulling it, so the MariaDB
     restart in step 4 is already clean.
  2. Copy `memory.cnf` to
     `/mnt/media/pi-media/containers/home-monitoring-db/config/memory.cnf`.
  3. Edit the live `pi-desktop` compose file's `home-monitoring-db` service to
     match the reference copy (environment variable, mount, `mem_limit`,
     `memswap_limit`).
  4. Recreate `home-monitoring-db`, which causes a brief database outage.
  5. Verify the change took effect:
     - `@@aria_pagecache_buffer_size` and `@@key_buffer_size` report 8 MB.
     - `MALLOC_ARENA_MAX` is present in the container environment.
     - `docker inspect` shows the 512 MB memory and memory+swap limits, and
       `docker stats --no-stream home-monitoring-db` shows a limit of `512MiB`,
       not `0B` (proof the kernel enforces it).
     - Both apps' next scheduled jobs record successful job runs.
- **No schema, data or API changes.** The app → database contract is unchanged.
- **Test-fixture Docker timeouts raised to 60 s (added mid-build, 2026-11-04).** CI runs
  on a self-hosted runner that is the Pi itself, and the real-MariaDB test fixtures
  give each Docker CLI call 10 s (`docker info`, `docker logs`, `docker port`) or 30 s
  (`docker run`, `docker exec`). On the memory-starved Pi those calls intermittently
  time out: PR #739's CI failed twice on a `docker info` and then a `docker logs`
  timeout. The errored test is not collected as run, which drops total coverage to
  95.82% against the 96% gate. Every Docker CLI timeout in the `libs/common` and
  `scripts` test fixtures becomes one named constant of 60 s per fixtures file.
  This is a CI-stability fix for the same root cause and is in this PR by the user's
  decision, even though it is unrelated to the config change itself. No new test: a
  timeout value has no behaviour a test could assert without testing the constant.
- **No ADR.** Every decision is a configuration value that's trivial to reverse.
  The "why" lives in comments beside each setting.

## Testing Decisions

- Tests verify observable behaviour and deployed configuration, not
  implementation details. In particular, the reconnect test asserts that an
  operation succeeds after the server drops the connection, not that a
  `pool_pre_ping` flag is set.
- **Seam A: static deployment-layout tests** (`scripts/tests/test_deployment_layout.py`).
  This reuses the existing compose-parsing and `.cnf`-parsing helpers, with the
  existing `logging.cnf` tests as prior art. New assertions:
  - The database service mounts `memory.cnf` read-only at
    `/etc/mysql/conf.d/memory.cnf`, from `containers/home-monitoring-db/config/`.
  - `memory.cnf`'s `[mysqld]` section sets `aria_pagecache_buffer_size` and
    `key_buffer_size` to 8M.
  - The database service's environment sets `MALLOC_ARENA_MAX` to 2.
  - The database service sets `mem_limit`, and `memswap_limit` equals it, so the
    container has no swap.
- **Seam B: real-MariaDB integration test** (`libs/common/tests`). This uses the
  existing `mariadb_container` fixture (a throwaway `mariadb:latest` container
  that skips when Docker is unavailable), with the existing schema-sync container
  tests as prior art.
  - Given a client that has already run a successful operation, when the server
    kills all of that client's connections (`KILL`, which looks the same to the
    client as a server restart), then the client's next operation succeeds
    without raising.
  - This test fails against today's engine and passes with `pool_pre_ping`.
- **Live verification (not automated).** These checks close the work:
  - The rollout checks listed above.
  - Seven days after rollout: `mariadbd` RSS on the Pi under 300 MB.
  - The existing slow query log (queries over 2 s) is scanned for new entries.
    This is for information only, given the memory-over-speed priority.

## Out of Scope

- Memory of other services on the Pi: InfluxDB (about 350 MB), the four
  self-hosted GitHub Actions runners (about 400 MB), Plex, Grafana, Telegraf.
- Memory limits or tuning for hive-app and octopus-app.
- The Pi's swap file size or swappiness.
- Shrinking the InnoDB buffer pool, connection limits or other MariaDB defaults
  beyond the two buffers named above.
- Switching allocator to jemalloc or tcmalloc (a fallback only if the 7-day check
  misses the target).
- A Grafana panel or alert for MariaDB memory.
- Pinning the `mariadb` image version.

## Further Notes

- Acceptance criteria:
  1. `mariadbd` RSS on the Pi is under 300 MB seven days after rollout (from about
     1.2 GB).
  2. `home-monitoring-db` runs with a 512 MB memory limit and no swap, and the
     kernel enforces it (`docker stats` shows `512MiB`, not `0B`).
  3. After a MariaDB restart, both apps' next database operation succeeds without
     a failed attempt.
  4. Data, schema and the app → database contract are unchanged.
- The PR merges before the 7-day check. That check closes the work. If it fails,
  the next step is the jemalloc fallback, recorded as a spec change first.
- Diagnosis was done read-only against the live Pi (`ssh pi@pi-desktop`). Reading
  `mariadbd`'s `/proc/<pid>/smaps` needed host `sudo`, because root inside the
  container lacks `CAP_SYS_PTRACE`.
- Expected footprint after the change: a startup baseline of about 250 MB (from
  about 470 MB), plus 2 bounded arenas, landing at about 250–300 MB.

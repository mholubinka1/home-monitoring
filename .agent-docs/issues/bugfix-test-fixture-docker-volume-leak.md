# Issues: bugfix-test-fixture-docker-volume-leak

## tests: MariaDB test fixtures leak a docker volume per run (Pi root disk filled) — [#572](https://github.com/mholubinka1/home-monitoring/issues/572)

**Blocked by**: None

**User stories**: 1, 2, 3

### What to build

Stop the MariaDB test fixtures leaking a docker anonymous volume per run.
Evidence from the Pi CI runner: root disk 100% full, 456 unused anonymous
volumes (79.7 GB, none attached to any container, created since Oct 1), CI
failing with "MariaDB container ... never became ready". Both fixtures
(`libs/common/tests/conftest.py`, `scripts/tests/conftest.py`) start with
`docker run --rm` but tear down with `docker rm -f <name>`; without `-v` the
image's anonymous `/var/lib/mysql` volume survives. Change teardown to
`docker rm -fv`, via a small helper per conftest, and add a docker-free test
that checks the teardown command forces removal and removes volumes.
Recommendations (a low-disk guard in CI, periodic `docker volume prune`) go in
the PR body, not in code.

### Acceptance criteria

- [ ] Given the libs/common fixture teardown, it removes the container with
      force and with volumes (`docker rm -fv`).
- [ ] Given the scripts fixture teardown, it removes the container with force
      and with volumes.
- [ ] The tests run without docker.
- [ ] The PR test plan states the on-Pi check: a CI run leaves
      `docker volume ls` unchanged.

---

# Issues: bugfix-fixture-setup-gap

## tests: MariaDB fixtures leak a container if docker run fails after creating it — [#580](https://github.com/mholubinka1/home-monitoring/issues/580)

**Blocked by**: None

### What to build

Move the `docker run` in both `mariadb_container` fixtures
(`libs/common/tests/conftest.py`, `scripts/tests/conftest.py`) inside the
`try`/`finally` whose `finally` removes the container with its volumes, so a
start that times out or errors after the daemon created the container is still
cleaned up.

### Acceptance criteria

- [ ] Given a `docker run` that fails after claiming the container name, when
      the `libs/common` fixture sets up, then `docker rm -fv <name>` still runs.
- [ ] The same holds for the `scripts` fixture.
- [ ] Both new tests fail against the old fixture ordering and pass against the
      new one.

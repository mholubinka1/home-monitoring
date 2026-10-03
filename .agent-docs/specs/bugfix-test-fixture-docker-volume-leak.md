# Test fixtures: stop leaking a docker volume per MariaDB test container

## Problem Statement

CI runs the docker-backed MariaDB tests on the self-hosted Pi runner. Each test container leaves behind an anonymous docker volume. After a couple of days there were 456 of them (79.7 GB), the Pi's root disk was 100% full, and CI failed with `MariaDB container ... never became ready` because a fresh container had no room to initialise. The same full disk put the production host at risk. The volumes were removed by hand, but nothing stops them accumulating again.

## Solution

The fixtures remove their container together with its volumes, so a test run leaves the docker volume list exactly as it found it.

## User Stories

1. As the operator, I want CI test runs to leave no docker volumes behind, so that the Pi's disk does not fill up.
2. As a contributor, I want a failing disk-full condition not to masquerade as a flaky MariaDB test, so that the real cause is visible.
3. As a maintainer, I want a test that fails if a fixture tears its container down without removing its volumes, so that the leak cannot return unnoticed.

## Implementation Decisions

- **Cause.** Both fixtures (`libs/common/tests/conftest.py`, `scripts/tests/conftest.py`) start the container with `docker run --rm` and tear it down with `docker rm -f <name>`. The mariadb image declares an anonymous `VOLUME /var/lib/mysql`; an explicit remove without `-v` deletes the container but not that volume, and it preempts `--rm`'s own cleanup.
- **Fix.** Teardown becomes `docker rm -fv <name>` (force, and remove anonymous volumes), via one small helper per conftest so it can be tested. Nothing else about the containers changes.
- **Not changed.** The `docker run` flags (no `--tmpfs`, no extra options), the readiness checks and the tests that use the fixtures.
- **Recommendations, not code (in the PR body).** A CI step that fails fast with a clear message when root free space is low, and/or a periodic `docker volume prune` on the runner.

## Testing Decisions

- The workstation has no docker, so the test must not need it. A docker-free test exercises each conftest's real teardown helper with `subprocess.run` replaced by a recorder, and asserts the command removes the container with force and with volumes.
- The real verification is on the Pi after merge: a CI run should leave the output of `docker volume ls` unchanged. That is stated in the PR test plan.

## Out of Scope

- Touching the Pi, pruning anything, or changing production containers.
- A disk-space guard in CI and any scheduled pruning (recommended only).
- Changing what the docker-backed tests assert.

## Further Notes

- Found while chasing a failing CI check on the heating-schedule PR. The disk had filled silently from Oct 1; the 80 GB were reclaimed manually on Oct 3 (100% to 33% used) after confirming every volume was anonymous and attached to no container.

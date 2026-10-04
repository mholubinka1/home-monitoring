# Issues: bugfix-ci-leaked-test-containers

## ci: a cancelled run leaks MariaDB test containers onto the runner — [#586](https://github.com/mholubinka1/home-monitoring/issues/586)

**Blocked by**: None

### What to build

Add a composite action, `.github/actions/remove-leaked-test-containers`, that
removes containers named exactly `common-schema-test-<8 hex>` or
`rename-script-test-<8 hex>` (with their volumes), warns instead of failing
when docker is unreachable or a removal fails, and prunes nothing. Use it in
`ci-arm64.yml` (the only workflow that runs pytest) after Checkout and before
the tests, as a backstop for a run killed too hard to clean up, and again as
the last step with `if: always()` and `continue-on-error: true`, so a
cancelled run still cleans up after itself. The disk guard stays the first
step.

### Acceptance criteria

- [ ] Given containers named like the fixtures' plus production and
      lookalike names, the sweep removes exactly the fixture-named ones, with
      `-f` and `-v`, in one `docker rm`.
- [ ] Given nothing to remove, no `docker rm` runs and the step passes.
- [ ] Given an unreachable docker daemon or a failing removal, the step warns
      (naming the containers left, for a failed removal) and does not fail the
      job.
- [ ] The script only lists and removes containers: it never prunes or touches
      volumes or anything else directly.
- [ ] The sweep's name pattern matches the names the two MariaDB test fixtures
      really generate (a test reads them from the conftest sources), and
      rejects wrong-length, non-hex, uppercase, prefixed and suffixed names.
- [ ] The build job runs the sweep after Checkout and before the tests, and
      again last with `if: always()`; both uses are `continue-on-error: true`;
      the disk guard is still the first step.
- [ ] `ci-checks.yml` needs no sweep because it never runs pytest; a test
      fails if that stops being true.
- [ ] Mutation-checked: loosening the name pattern or its length, breaking a
      prefix, or changing `always()` to `success()`, fails a test.

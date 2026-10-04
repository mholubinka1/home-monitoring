# Issues: bugfix-ci-leaked-test-containers

> Work complete — [PR #587](https://github.com/mholubinka1/home-monitoring/pull/587) ready to merge.

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

- [x] Given containers named like the fixtures' plus production and
      lookalike names, the sweep removes exactly the fixture-named ones, with
      `-f` and `-v`, in one `docker rm`.
- [x] Given nothing to remove, no `docker rm` runs and the step passes.
- [x] Given an unreachable docker daemon or a failing removal, the step warns
      (naming only the containers actually still there, for a failed or
      partial removal) and does not fail the job. A container that exited by
      itself between the listing and the removal is not blamed on the
      operator.
- [x] The script only lists and removes containers: it never prunes or touches
      volumes or anything else directly.
- [x] The sweep's name pattern matches the names the two MariaDB test fixtures
      really generate (a test reads them from the conftest sources, and also
      checks the disk guard's hint names the same prefixes), and rejects
      wrong-length, non-hex, uppercase, prefixed and suffixed names.
- [x] The build job runs the sweep after Checkout and before the tests, and
      again last with `if: always()`; both uses are `continue-on-error: true`;
      the disk guard is still the first step.
- [x] `ci-checks.yml` needs no sweep because it never runs pytest; a test
      fails if that stops being true.
- [x] Mutation-checked: loosening the name pattern or its length, breaking a
      prefix, or changing `always()` to `success()`, fails a test.

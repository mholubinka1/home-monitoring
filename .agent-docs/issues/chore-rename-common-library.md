# Issues: chore/rename-common-library

## Rename the shared library's distribution to monitoring-common

**Blocked by**: None

**User stories**: 2, 3, 4, 5, 6

### What to build

Rename the shared library's distribution from `common` to `monitoring-common`, keeping
the import name `common`, so no Python source file changes. The library tells its build
tool which folder to ship, with a short comment on why the names differ. Both apps depend
on the new name, `uv.lock` is regenerated, and the Dockerfile comments, the glossary
entry and ADR-0020 are brought in line.

### Acceptance criteria

- [ ] Given the rename, when `uv lock --check` and `uv sync --locked --all-packages` run,
      then both succeed.
- [ ] Given the rename, when code runs `from common import local_day`, then it resolves to
      `libs/common/common`, and the installed distribution is `monitoring-common` 0.1.0.
- [ ] Given the regenerated `uv.lock`, then only the rename lines differ, with no version
      bumps.
- [ ] Given each Dockerfile's dependency step run on just the files it copies, then the
      library installs and `common` imports, for both apps.
- [ ] Given the full test suite and every pre-commit hook, then they pass, and CI builds
      both images.
- [ ] Given the docs, then the glossary entry and ADR-0020 explain why the distribution is
      `monitoring-common` but is imported as `common`.

---

## Confirm Dependabot no longer errors on the library

**Blocked by**: #707

**User stories**: 1

### What to build

After the rename is merged, run a Dependabot "Check for updates" (Insights > Dependency
graph > Dependabot) or use the next weekly run, and read its log. No code change unless
the error persists.

### Acceptance criteria

- [ ] Given the merged rename and a Dependabot check, then the log has no "Handled error
      whilst updating" for the library and the run succeeds.
- [ ] Given the error persists, then it is reported and an `ignore` entry for
      `monitoring-common` in `.github/dependabot.yml` is proposed.

---

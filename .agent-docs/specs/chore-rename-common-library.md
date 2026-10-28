# Rename the shared library's distribution to monitoring-common

## Problem Statement

The "Dependabot Updates" job has failed on most days since 25 September 2026, two days
after the shared library `libs/common` was added to the uv workspace. Dependabot reads
`uv.lock`, sees the workspace member `common` 0.1.0, and looks it up on PyPI, where an
unrelated package called `common` exists (0.1.2, "Common tools and data structures
implemented in pure python"). It decides `common` needs updating and `uv` cannot resolve
that: "No solution found when resolving dependencies for split (markers:
python_full_version >= '3.15') ... your workspace requires common". Every failed run
sampled, nine between 25 September and 27 October, logs "Handled error whilst updating
common".

The cost is a red run on most days, which hides a real Dependabot failure. Update and
security PRs are still created. There is also a latent hazard: anything that resolves
`common` outside the workspace, such as a bare `pip install` of an app, would install the
PyPI package instead of this library.

## Solution

Rename the library's **distribution name** (the name uv, pip and Dependabot see) from
`common` to `monitoring-common`. Its **import name** stays `common`, so no Python source
file changes. Dependabot then looks up `monitoring-common`, finds nothing on PyPI, and
skips it, as it already does for the root project `home-monitoring`.

## User Stories

1. As the repo owner, I want the Dependabot Updates job to stop failing on `common`, so
   that a red run means something real is wrong.
2. As the repo owner, I want the shared library's name to be unique on PyPI, so that
   installing an app outside the workspace can never pull in an unrelated package.
3. As a developer, I want `from common import ...` to keep working unchanged, so that the
   rename does not touch the 59 Python files that import it.
4. As a developer, I want `uv sync` and both Docker image builds to keep working, so that
   the rename does not break local setup or deployment.
5. As the repo owner, I want `uv.lock` to change only by the rename, so that the diff is
   easy to review and carries no unrelated version bumps.
6. As a future reader, I want a note explaining why the distribution is called
   `monitoring-common` but imported as `common`, so that the mismatch does not look like a
   mistake.

## Implementation Decisions

- **Library name.** In `libs/common/pyproject.toml`, `name` becomes `monitoring-common`.
  The new name was chosen by the owner and is free on PyPI. The owner was told it is
  generic enough that a package with that name could appear later.
- **Build setting.** The library is built with `hatchling`, which by default ships a
  folder named after the distribution (`monitoring_common/`). The change adds
  `[tool.hatch.build.targets.wheel] packages = ["common"]` with a short comment saying why
  the names differ. Without it `uv sync` fails with "Unable to determine which files to
  ship inside the wheel". This was reproduced in a trial.
- **Dependants.** `apps/octopus-app` and `apps/hive-app` depend on `monitoring-common`,
  and their `[tool.uv.sources]` workspace entry is keyed by the new name.
- **Lock file.** `uv.lock` is regenerated with `uv lock`. In the trial only the rename
  lines moved: the `common` package entry and the two dependency references.
- **Dockerfiles.** Both keep their steps. They copy `libs/common/pyproject.toml` and
  `libs/common/common` before `uv sync`, which already satisfies the new build setting.
  Their comments that call `common` a dependency are refreshed to name the distribution.
- **Docs.** The glossary entry for `common` says it is imported as `common` and installed
  as `monitoring-common`, and why. ADR-0020 gets a one-line note about the rename. No new
  ADR: the change is easy to reverse.
- **Unchanged.** The folder `libs/common/common/`, the import name, and every config that
  refers to it as a path or import (pytest `pythonpath`, isort `known_first_party`,
  coverage `source`, mypy `mypy_path`, the CI mypy path, the Docker `COPY` paths).

## Testing Decisions

- This is packaging, not logic, so no new unit test is written. The owner was offered a
  guard test against reverting to a bare `common` and declined it.
- What makes a good check here: it exercises the real install and build path, not the file
  contents.
- Verification, in order:
  1. `uv lock --check` and `uv sync --locked --all-packages` succeed (both are CI steps),
     and `import common` resolves to `libs/common/common`.
  2. The full pytest suite and every pre-commit hook pass.
  3. The Dockerfiles' dependency step is run in a scratch folder holding only the files
     they copy, for both apps (passed in the trial).
  4. CI builds both images without pushing, because the change touches image inputs.
  5. After merge, a Dependabot "Check for updates" run no longer logs "Handled error
     whilst updating common". Merging does not trigger a run by itself, so the owner
     triggers it (Insights > Dependency graph > Dependabot) or the next weekly run
     provides it, and the log is read.

## Out of Scope

- Renaming the import package or the `libs/common/common/` folder.
- Any change to `.github/dependabot.yml`, including an `ignore` entry.
- Any change to the apps' runtime behaviour.

## Further Notes

- Open branches (#687 and the wave-2 branches) are unaffected unless they edit the
  `pyproject.toml` files or `uv.lock`.
- If the Dependabot log still shows an error about the library after merge, the next
  option is an `ignore` entry for `monitoring-common` in `.github/dependabot.yml`; with
  the rename it should not be needed.

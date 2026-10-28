# uv workspace for per-app packaging, one lockfile

With `libs/common` (ADR-0020) now shared between `octopus-app` and `hive-app`, packaging needed a way to keep each container image lightweight — `octopus-app`'s image must not carry `pyhive-integration`, and `hive-app`'s must not carry Octopus-only deps — while still sharing one dependency-resolution source of truth. The repo becomes a uv workspace: the root `pyproject.toml` is a workspace root with no dependencies of its own; `apps/octopus-app/`, `apps/hive-app/`, and `libs/common/` each get their own `pyproject.toml` declaring only the dependencies they actually use, plus a workspace path-dependency on `monitoring-common` (imported as `common`) where needed. One `uv.lock` at the root keeps versions consistent across all three; `uv sync --package <app>` installs only that member's dependency closure, so each Dockerfile builds a lean, app-scoped image.

Tests move inside each package (`apps/octopus-app/tests/`, `apps/hive-app/tests/`, `libs/common/tests/`), but CI and local dev still run one combined `pytest`/coverage pass across the whole workspace rather than per-package runs, so a change to `libs/common` that breaks one app's tests is caught in the same run it's introduced.

## Considered Options

- **Fully separate projects with duplicated lockfiles** — rejected: `common` would need its own versioning/publishing story (a private package index, or git-dependency pins) instead of just being a workspace member, adding release-coordination overhead disproportionate to two containers on one Pi.
- **Single top-level project, no workspace, two entry points** — rejected: without per-member `pyproject.toml`s there's no way to scope `uv sync` to only one app's dependencies, so every image would end up installing every dependency either app needs.

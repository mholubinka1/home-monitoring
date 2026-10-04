# Rename the Docker Hub image to `octopus-app`, no namespace prefix

The live Pi still pulls `mholubinka1/octopus-monitoring:latest` — a name left over from before the repo hosted two containers ([#496](https://github.com/mholubinka1/home-monitoring/issues/496) renamed the GitHub repo itself; the image name is the still-open step [ADR-0022](0022-single-shared-home-monitoring-database.md) and `.agent-docs/context.md` flag alongside the database rename). Once `hive-app` exists as its own image (`mholubinka1/hive-app`), `octopus-monitoring` both misdescribes what the container does and no longer pairs sensibly with its sibling's name.

The decision: rename the image to `mholubinka1/octopus-app`, matching the `octopus-app` package/directory name (ADR-0021) and the plain, unprefixed style already used for `hive-app`'s image.

This rename executes together with the database rename ([ADR-0022](0022-single-shared-home-monitoring-database.md)) in one confirmed Pi cutover window, not staggered — see the migration script at `scripts/rename_database.sql` and the runbook, retired after the cutover and kept in [git history](https://github.com/mholubinka1/home-monitoring/blob/9522fda/deployments/mariadb/RENAME_RUNBOOK.md).

## Considered Options

- **`mholubinka1/octopus-app` (chosen)** — mirrors `hive-app`'s own image name and the package directory name; no new naming convention introduced.
- **A repo-wide namespace prefix, e.g. `mholubinka1/home-monitoring/octopus-app`** — rejected: Docker Hub already scopes images by account (`mholubinka1/`), so a repo-name prefix adds a segment with no information the account doesn't already provide, and neither existing image (`hive-app`) uses one.
- **Keep `octopus-monitoring`** — rejected: the name predates `hive-app` and permanently misdescribes the image once a second container exists, same reasoning ADR-0022 used to reject keeping the `octopus` database name.

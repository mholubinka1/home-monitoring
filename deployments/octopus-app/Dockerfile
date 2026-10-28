FROM python:3.13.14-alpine3.24

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apk add --no-cache --virtual .deps g++ gcc musl-dev python3-dev libffi-dev openssl-dev cargo pkgconfig
RUN apk add libpq-dev

WORKDIR /app

# uv needs every workspace member's own pyproject.toml to resolve the lockfile's
# dependency graph, even members not being installed into this image -- so
# apps/hive-app/pyproject.toml is copied here too, but not its source.
COPY pyproject.toml uv.lock ./
COPY apps/octopus-app/pyproject.toml apps/octopus-app/pyproject.toml
COPY apps/hive-app/pyproject.toml apps/hive-app/pyproject.toml
COPY libs/common/pyproject.toml libs/common/pyproject.toml
# monitoring-common (imported as `common`) is an actual (non-skipped) dependency of octopus-app, so uv sync builds
# and editable-installs it below -- its source must exist before that step, unlike
# octopus-app's own source (skipped by --no-install-project, only needed at runtime).
COPY libs/common/common ./libs/common/common

RUN uv sync --frozen --no-dev --no-install-project --package octopus-app

RUN apk del .deps

COPY apps/octopus-app/octopus_app ./apps/octopus-app/octopus_app

# monitoring-common is editable-installed into the venv by uv sync above (a real workspace
# dependency, resolved from its own pyproject.toml), so it's importable as a
# plain top-level `common` without any extra PYTHONPATH entry -- only
# octopus_app needs one, since --no-install-project skipped installing it.
ENV PYTHONPATH=/app/apps/octopus-app

RUN mkdir -p config
VOLUME /config

USER 999

ENV PATH="/opt/venv/bin:${PATH}"

CMD [ "python", "-m", "octopus_app.main", "--config-file", "/config/config.yml"]

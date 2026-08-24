FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    cron \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PYTHON_DOWNLOADS=never

# README.md included here because hatchling (our build backend) validates
# project metadata -- including the readme file's existence -- before it
# will build the package at all, even for `uv sync --no-dev`.
COPY pyproject.toml uv.lock .python-version README.md ./


# ---------------------------------------------------------------------------
# prod: lean runtime image, no dev dependency-group. Used by docker-compose.yaml.
# ---------------------------------------------------------------------------
FROM base AS prod
RUN uv sync --frozen --no-dev
COPY src/ ./src/
COPY migrations/ ./migrations/
RUN uv sync --frozen --no-dev
CMD ["vpic-update"]


# ---------------------------------------------------------------------------
# dev: adds the "dev" dependency-group (pytest/ruff/requests-mock) for
# local testing. Used by docker-compose.dev.yaml. Not used in any deployed
# environment.
# ---------------------------------------------------------------------------
FROM base AS dev
RUN uv sync --frozen --group dev
COPY src/ ./src/
COPY tests/ ./tests/
COPY migrations/ ./migrations/
RUN uv sync --frozen --group dev
CMD ["pytest", "-v"]
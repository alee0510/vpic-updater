FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    cron \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./


# ---------------------------------------------------------------------------
# prod: lean runtime image, no test tooling. Used by docker-compose.yaml.
# ---------------------------------------------------------------------------
FROM base AS prod
RUN uv sync --frozen --no-dev
COPY src/ ./src/
COPY migrations/ ./migrations/
RUN uv sync --frozen --no-dev
CMD ["vpic-update"]


# ---------------------------------------------------------------------------
# dev: adds pytest/ruff/requests-mock for local testing. Used by
# docker-compose.dev.yaml. Not used in any deployed environment.
# ---------------------------------------------------------------------------
FROM base AS dev
RUN uv sync --frozen --extra dev
COPY src/ ./src/
COPY tests/ ./tests/
COPY migrations/ ./migrations/
RUN uv sync --frozen --extra dev
CMD ["pytest", "-v"]
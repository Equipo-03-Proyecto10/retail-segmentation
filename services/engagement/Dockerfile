# A Flask service's image (ADR-0040), rendered by render.py into
# services/engagement/Dockerfile. The build context is services/engagement/ and
# nothing above it (ADR-0045): CI copies contracts/engagement/ and
# contracts/common/v1/ into services/engagement/contracts/ (git-ignored) before
# `docker build`, and this file copies them from inside that context.
#
# Layout it expects: pyproject.toml and uv.lock (mosaiq-kit pinned by tag, see
# packages/kit-py/README.md), the code in app/ exposing create_app(), and
# contracts/.

FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS build
# git fetches mosaiq-kit from its tag; it stays in this stage.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.12.3
WORKDIR /app
COPY pyproject.toml uv.lock ./
ENV UV_PROJECT_ENVIRONMENT=/app/.venv UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY app/ ./app/
COPY contracts/ ./contracts/

USER 10001:10001
EXPOSE 5012
# One worker, four threads, inside the 160 MB of ADR-0040's budget.
CMD ["gunicorn", "--bind", "0.0.0.0:5012", "--workers", "1", "--threads", "4", "app:create_app()"]

# ops-console's image (ADR-0055), after deploy/templates/service/Dockerfile.python.
# The build context is ops/console/ and nothing above it: CI copies
# contracts/registry.yaml into ops/console/registry.yaml (git-ignored) first.

FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS build
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
COPY registry.yaml ./registry.yaml

USER 10001:10001
EXPOSE 5090
# One worker, so one poller; its threads serve the page while it polls.
CMD ["gunicorn", "--bind", "0.0.0.0:5090", "--workers", "1", "--threads", "2", "app:create_app()"]

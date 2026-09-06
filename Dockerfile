# MOSAIQ — application image.
#
# One of the two execution paths in ADR-0006. The other is gunicorn under
# systemd on the instance, which is the default there. Both install from
# web/requirements.txt and both bind :8000, so NGINX is indifferent to which
# one is running.
#
# Python 3.12 to match docs/scope.md §3 and the version CI runs.
FROM python:3.12-slim

# Keeps the image quiet and predictable: no .pyc files written into the
# layer, stdout unbuffered so `docker compose logs` shows output as it
# happens rather than when a buffer fills.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, in their own layer: they change far less often than the
# application, so editing a template does not reinstall Flask.
COPY web/requirements.txt web/requirements.txt
RUN pip install --no-cache-dir -r web/requirements.txt

COPY web/ web/

# Not root. The application reads its configuration and writes uploads; it has
# no reason to be able to write anything else.
RUN useradd --create-home --uid 10001 mosaiq \
    && mkdir -p web/uploads \
    && chown -R mosaiq:mosaiq /app
USER mosaiq

EXPOSE 8000

# 0.0.0.0 inside the container; compose publishes it on the host's loopback
# only. The image carries no connection string — DATABASE_URL arrives from the
# environment, which is what lets this same image talk to the db service
# locally and to the instance's own PostgreSQL when it runs there.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--access-logfile", "-", "web.app:create_app()"]

# S0-02: python:3.12-slim, not Alpine — scikit-learn and pandas publish no
# musl wheels, so Alpine forces source compilation and multi-minute rebuilds.
FROM python:3.12-slim

WORKDIR /app

RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --create-home --shell /bin/bash app

# Dependencies copied and installed before the rest of the source so this
# layer is cached across source-only changes.
COPY web/requirements.txt web/requirements.txt
RUN pip install --no-cache-dir -r web/requirements.txt

COPY web/ web/
COPY infra/sql/ infra/sql/

RUN chown -R app:app /app
USER app

# Placeholder: the Flask application factory does not exist yet (S0-03).
# Whoever picks up S0-03 replaces this CMD with gunicorn/flask run and
# replaces the compose healthcheck with a real HTTP check against it.
CMD ["tail", "-f", "/dev/null"]

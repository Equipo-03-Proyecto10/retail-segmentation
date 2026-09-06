# Retail Segmentation Platform

Monolithic web application for retail customer segmentation management.
Server-rendered Flask over PostgreSQL, deployed on a single GCP Compute Engine
instance.

Course project — Integración de Aplicaciones Computacionales, Team 03.
Everything written is in English.

> The product is **MOSAIQ**, with the design system recorded in
> [ADR-0002](docs/adr/0002-mosaiq-identity-and-design-system.md).

## Team

| Person | Role |
|---|---|
| Dr. Raúl Morales Saucedo | Product Owner |
| Raquel | Proxy PO, Developer |
| Estefanía | Data modeling |
| Max | Infrastructure, deployment |
| Marcelo | Scrum Master, Full stack |

## Scope

One deployable application. No external APIs, no microservices, no JSON or XML
between internal components, one database engine, one administrator user. The
full boundary is in [`docs/scope.md`](docs/scope.md).

The RFM, clustering and segment-migration analytics are deferred to a later
delivery — see [`docs/roadmap.md`](docs/roadmap.md).

## Stack

Python 3.12 · Flask + Jinja2 · PostgreSQL · Gunicorn under systemd · NGINX or
Apache as reverse proxy · CentOS 10 Stream on GCP Compute Engine.

Flask rather than the Node.js the exercise statement illustrates:
[ADR-0001](docs/adr/0001-flask-monolith-on-a-single-vm.md).

## Structure

| Directory | Contents |
|---|---|
| `docs/` | Documentation, decisions, evidence |
| `sql/` | `00_create_database.sql`, `01_schema.sql`, `02_seed_30_per_table.sql` |
| `web/` | The application, organized by layers |

## Running it

Two ways, and the application behaves identically under both — it reads the same
configuration from the environment either way. Containers for local work,
gunicorn under systemd on the instance, which is the default there:
[ADR-0006](docs/adr/0006-run-under-both-systemd-and-docker-compose.md).

### With containers

Requires Docker with the Compose plugin. Nothing else — no Python, no local
PostgreSQL.

```bash
cp .env.example .env
docker compose up
```

That creates the database, applies the schema, loads the seed data and starts
the application, in that order. It is then at http://localhost:8000

```bash
docker compose down       # stop, keeping the data
docker compose down -v    # stop and discard the database volume
```

The database is published on `127.0.0.1:5432`, so `psql -h localhost -U postgres
-d retail` reaches it from the host.

### Without containers

Requires Python 3.12 and a local PostgreSQL.

```bash
cp .env.example .env          # adjust the connection string

psql -U postgres -f sql/00_create_database.sql
psql -U postgres -d retail -f sql/01_schema.sql
psql -U postgres -d retail -f sql/02_seed_30_per_table.sql

python -m venv .venv && source .venv/bin/activate
pip install -r web/requirements.txt
flask --app web.app run
```

The application is then at http://localhost:5000

The three scripts must run in that order against an empty database — that is
Definition of Done item 3, and CI checks it on every pull request.

### Signing in

The seed creates thirty accounts, all with the password `Password123!`, hashed
with argon2id. `admin@mosaiq-demo.com` is the administrator, and there is
exactly one. The rest are `user2@…` through `user30@…`, spread across the other
six roles.

Demonstration data only. Nothing here is a secret and nothing here belongs on
the instance.

Run the checks the pipeline runs with `pytest`, `black --check .` and
`ruff check .`, after `pip install -r web/requirements-dev.txt`.

Using an AI coding agent? Also run `touch ~/.claude/rs-local.md` so the
personal-context import resolves.

## Documentation

Start at [`docs/README.md`](docs/README.md).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). `main` is protected and `develop`
requires a reviewed pull request.

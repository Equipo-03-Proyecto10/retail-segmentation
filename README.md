# Retail Segmentation Platform

Monolithic web application for retail customer segmentation management.
Server-rendered Flask over PostgreSQL, deployed on a single GCP Compute Engine
instance.

Course project — Integración de Aplicaciones Computacionales, Team 03.
Everything written is in English.

> **Company name and design system are not decided yet.** They are tracked as
> Q-3 and Q-4 in [`docs/scope.md`](docs/scope.md) §8.

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

## Running it locally

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

> The application is being built. `sql/` and `web/` are produced by the Phase 2
> and Phase 3 stories in [`docs/backlog.md`](docs/backlog.md); until those land,
> the commands above describe the target rather than the current state.

Using an AI coding agent? Also run `touch ~/.claude/rs-local.md` so the
personal-context import resolves.

## Documentation

Start at [`docs/README.md`](docs/README.md).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). `main` is protected and `develop`
requires a reviewed pull request.

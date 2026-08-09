# Retail Segmentation and Personalization Platform

Dynamic customer segmentation platform that recomputes RFM-based segments as
purchase behaviour changes, without losing historical traceability of past
assignments.

Course project — Integración de Aplicaciones Computacionales, Team 03.
All code, comments, commits, and documentation are written in English.

## Team

| Person | Role | Primary area |
|---|---|---|
| Dr. Raúl Morales Saucedo | Product Owner | Requirements, acceptance |
| Raquel | Proxy PO, Developer | Flask web system, Jinja2, Highcharts |
| Estefanía | ML / Data | RFM, clustering, drift, data modeling |
| Max | Dev / DevOps | Containers, GCP, databases |
| Marcelo | Scrum Master, Full stack | Auth, DevOps, integration |

## Architecture

Monorepo. Four deployable components share one PostgreSQL / MongoDB / Redis
backing layer, with each table owned by exactly one writer (see
`docs/adr/0002-data-ownership-map.md`).

| Directory | Component | Milestone |
|---|---|---|
| `web/` | Flask + Jinja2 enterprise web system | M1 |
| `services/` | Flask REST microservices, JSON and XML | M2 |
| `mobile/` | Android consumer client, JSON only | M3 |
| `desktop/` | Analyst client, XML only | M3 |
| `ml/` | RFM, clustering, drift detection, seeding | M1–M3 |
| `infra/` | Containers, GCP deployment | M1–M3 |

`services/_shared/` holds the JWT validation, correlation IDs, error envelope,
content negotiation, and health endpoints that all thirteen microservices
reuse. Never copy that code into an individual service.

## Quickstart

Requires Docker, Docker Compose, and Python 3.12.

```bash
cp .env.example .env
make up        # PostgreSQL, MongoDB, Redis, web
make migrate   # alembic upgrade head
make seed      # load transaction history
```

The web system is then at http://localhost:5000

Using an AI coding agent? Also run `touch ~/.claude/rs-local.md` so the
personal-context import resolves.

## Documentation

Start at [`docs/README.md`](docs/README.md).

| Topic | Location |
|---|---|
| Scrum process, sprints, risks | [`docs/scrum/`](docs/scrum/) |
| Architecture decisions | [`docs/adr/`](docs/adr/) |
| Database designs | [`docs/data/`](docs/data/) |
| Requirements and user stories | [`docs/requirements/`](docs/requirements/) |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). `main` is protected; work happens on
feature branches merged into `develop`.

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

**First delivery — complete.** One deployable application. No external APIs, no
microservices, no JSON or XML between internal components, one database engine,
one administrator user. The boundary is in [`docs/scope.md`](docs/scope.md).

**Second delivery — analytics phase underway.** The RFM, clustering and
segment-migration work the first delivery deferred is now being built into the
same Flask application: sales ingestion, segmentation runs with history,
recommendations, campaigns, experiments and dashboards. Its boundary is in
[`docs/scope-delivery-2-analytics.md`](docs/scope-delivery-2-analytics.md) and
its phases in [`docs/roadmap.md`](docs/roadmap.md).

[ADR-0016](docs/adr/0016-the-second-delivery-reinstates-the-distributed-architecture.md)
lifts the first delivery's no-API, no-microservice constraints for the second
delivery, with the monolith as one of its components. None of the distributed
components exist yet, and none is built before the ADR that defines its
contract. There is still exactly one administrator.

## Stack

Python 3.12 · Flask + Jinja2 · PostgreSQL · Gunicorn under systemd · NGINX as
reverse proxy ([ADR-0009](docs/adr/0009-nginx-as-the-reverse-proxy.md)) ·
Highcharts for the dashboards · CentOS 10 Stream on GCP Compute Engine.

Flask rather than the Node.js the exercise statement illustrates:
[ADR-0001](docs/adr/0001-flask-monolith-on-a-single-vm.md), superseded for the
second delivery by ADR-0016, which keeps the monolith. K-means is written in the
application, not taken as a dependency
([ADR-0021](docs/adr/0021-k-means-is-implemented-in-the-application-rather-than-taken-as-a-dependency.md)),
so `web/requirements.txt` has not grown.

## Structure

| Directory | Contents |
|---|---|
| `deploy/` | Instance deployment: systemd unit, NGINX configuration, scripts ([`deploy/README.md`](deploy/README.md)) |
| `docker/` | The init script that runs the three SQL scripts in the Compose database |
| `docs/` | Documentation, decisions, evidence |
| `sql/` | `00_create_database.sql`, `01_schema.sql`, `02_seed_30_per_table.sql`, plus `verify_integrity.sql` and `seed-exempt.txt` |
| `tests/` | The `pytest` suite CI runs |
| `web/` | The application, organized by layers |

## Analytics modules

Server-rendered pages in the same application, each gated by a permission. Which
roles reach which page is in
[`docs/analytics-permission-map.md`](docs/analytics-permission-map.md).

| Page | What it does | Decision |
|---|---|---|
| `/admin/sales-import/` | Import sales from a versioned CSV, with a row-level acceptance and rejection report | [ADR-0020](docs/adr/0020-csv-is-the-sole-sales-ingestion-entry-point-for-this-delivery.md) |
| `/segment-run/` | Run `RFM_RULES` or `KMEANS` segmentation over a chosen window | [ADR-0017](docs/adr/0017-segment-assignment-history-replaces-the-mutable-current-segment.md), [ADR-0030](docs/adr/0030-k-means-clusters-are-paired-with-labels-by-proportional-rank.md) |
| `/run-history/`, `/segment-history-report/` | Past runs with their parameters and quality measures; a customer's assignment history | ADR-0017 |
| `/migration-matrix/`, `/migration-explanation/`, `/model-comparison/` | Segment migration between runs, and the two strategies compared through stable labels | ADR-0030 |
| `/consumption-reports/` | Consumption profiles and shifts by store, channel and category | — |
| `/catalog/` | Adds per-customer product recommendations to the consultation module | — |
| `/campaigns/`, `/experiments/`, `/experiment-report/` | Campaigns, and experiments with separate assignment, exposure and conversion | [ADR-0019](docs/adr/0019-experiment-measurement-separates-assignment-exposure-and-conversion.md) |
| `/segmentation-dashboard/` | Segment sizes, RFM distribution, migration and revenue by segment | — |

While `DATA_IS_SYNTHETIC=true`, the default, the dashboard labels its figures
Synthetic: the seed is demonstration data.

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
docker compose up --build
```

That creates the database, applies the schema, loads the seed data and starts
the application, in that order. It is then at http://localhost:8000

**`--build` is not optional after the first time.** Plain `docker compose up`
reuses the image already on the machine, so on a second run it starts a
container built from whatever the source looked like when you last built it.
It reports success and serves stale code; `docker compose down -v` does not
help, because the staleness is in the image rather than the container or the
volumes.

```bash
docker compose down       # stop, keeping the data
docker compose down -v    # stop and discard the database volume
```

**After a schema or seed change, discard the volume.** The SQL scripts run only
when the database volume is empty, so a volume created before `sql/` changed
keeps the old schema, and `--build` does not touch it. The application then
starts against the old tables and errors on every page that needs the new
ones. When a pull of `develop` touches `sql/`, run `docker compose down -v`
before `docker compose up --build`.

The database is published on `127.0.0.1:5432`, so `psql -h localhost -U postgres
-d retail` reaches it from the host.

To exercise the NGINX reverse proxy locally, as it sits on the instance
([ADR-0009](docs/adr/0009-nginx-as-the-reverse-proxy.md)):

```bash
docker compose -f compose.yaml -f compose.proxy.yaml up
```

The application is then behind NGINX at http://localhost:8080. Plain
`docker compose up` is unchanged.

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

Sign-in is rate limited: after `LOGIN_THROTTLE_MAX_ATTEMPTS` failures (5 by
default) within `LOGIN_THROTTLE_WINDOW_SECONDS` (15 minutes), the application
answers 429 until the window passes. Behind NGINX, the proxy also limits
`POST /login` per client address.

Demonstration data only. Nothing here is a secret and nothing here belongs on
the instance.

Run the checks the pipeline runs with `pytest`, `black --check .` and
`ruff check .`, after `pip install -r web/requirements-dev.txt`.

Using an AI coding agent? Also run `touch ~/.claude/rs-local.md` so the
personal-context import resolves.

### On the instance

The seed's thirty accounts all share the password above, which is fine locally
and is a hole on a published host. Before an instance is reachable from
outside, one command gives it a real administrator and closes the seeded
logins:

```bash
flask --app web.app provision-administrator \
    --name "Real Person" --email person@udem.edu --deactivate-demo-accounts
flask --app web.app account-report   # "Demonstration accounts that can still sign in: 0"
```

The procedure, and what it does about the single-administrator rule, is in
[`docs/runbook-instance-accounts.md`](docs/runbook-instance-accounts.md).

## Documentation

Start at [`docs/README.md`](docs/README.md). Deploying on the instance:
[`deploy/README.md`](deploy/README.md).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). `main` is protected and `develop`
requires a reviewed pull request.

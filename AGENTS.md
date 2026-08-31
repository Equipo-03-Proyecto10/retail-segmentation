# Agent Instructions

Retail segmentation platform. One monolithic Flask web application on a single
GCP Compute Engine instance, backed by PostgreSQL on the same machine. Course
project, Team 03.

Read [`docs/scope.md`](docs/scope.md) before starting anything. It is the
boundary of the delivery.

## Language

Every artifact is written in English: code, comments, commit messages, branch
names, issue titles, documentation. Spoken meetings are in Spanish; nothing
written is.

## Hard rules

**One application.** No microservices, no external REST/GraphQL/SOAP APIs, and
no JSON or XML exchanged between internal components. Pages are server-rendered
with Jinja2; the browser receives HTML. Adding a second deployable unit is a
scope change and needs an ADR.

**Schema changes go in `sql/01_schema.sql`.** Never alter a table by hand on the
server and never write DDL anywhere else. The three scripts —
`00_create_database.sql`, `01_schema.sql`, `02_seed_30_per_table.sql` — must run
clean in that order against an empty PostgreSQL. A change that breaks a clean
run is a broken change, not a migration problem.

**The model is normalized to 4NF, and each normalization decision is written
down.** The justification is a graded deliverable, not a comment.

**Seed data keeps at least 30 rows per table.** A new table ships with its seed
rows in the same pull request.

**There is exactly one administrator.** Enforced twice: refused by the
application, and refused by a partial unique index in the schema. The
application check alone is bypassed by a direct `INSERT`; the index alone
surfaces as an unexplained database error. Both, or neither counts.

**Every SQL statement is parameterized.** No string interpolation into SQL, ever,
including in scripts and one-off queries.

**No secrets in source.** New configuration goes in `.env.example` with a safe
default.

**Never commit datasets or uploaded files.** `data/`, `*.csv` and `web/uploads/`
are gitignored.

## Conventions

- Python: PEP 8, `black`, `ruff`. Tables are snake_case and singular.
- Commits: Conventional Commits, `type(scope): subject (#issue)`.
  Example: `feat(auth): add password reset (#42)`
- Branches: `feature/<issue-number>-kebab-slug`, also `fix/`, `chore/`, `docs/`.
- `main` is protected. `develop` is the integration branch and also requires a
  reviewed pull request.

## Before reporting a task complete

1. `pytest` passes.
2. `black --check .` and `ruff check .` pass.
3. Any schema change is reflected in `sql/01_schema.sql`, and the three scripts
   still run clean in order from an empty database.
4. New configuration is documented in `.env.example`.
5. The Definition of Done in [`docs/process.md`](docs/process.md) §6 is met.

## Where decisions live

Architectural decisions go in [`docs/adr/`](docs/adr/) as numbered ADRs. ADRs
are immutable once accepted — supersede, never edit. Do not make an
architectural decision silently in code; write the ADR or ask.

Work deferred to a later delivery is recorded in
[`docs/roadmap.md`](docs/roadmap.md). Do not build it early.

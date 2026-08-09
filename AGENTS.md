# Agent Instructions

Retail segmentation platform. Monorepo, four deployable components, one shared
data layer. Course project, Team 03.

## Language

Every artifact is written in English: code, comments, commit messages, branch
names, issue titles, documentation. Spoken meetings are in Spanish; nothing
written is.

## Hard rules

**Segment assignments are never updated in place.** There is no mutable
`customer.segment_id`. A new segmentation run closes the previous assignment by
setting `valid_to` and inserts a new row in `customer_segment_assignment`. A
partial unique index enforces one open assignment per customer. Losing this
breaks the project's central requirement. Read
`docs/adr/ADR-002-data-ownership.md` and the ERD before touching segment data.

**Schema changes ship as Alembic migrations.** Never edit a table by hand or
write raw DDL outside a migration. `alembic upgrade head` must succeed against
an empty database.

**State-changing operations write an audit log record** with actor, action,
entity, timestamp, and correlation id.

**One writer per table.** Check `docs/adr/ADR-002-data-ownership.md` before
writing to any store from a new component. Cross-component writes are
prohibited; the owner exposes an endpoint instead.

**No secrets in source.** New configuration goes in `.env.example` with a safe
default.

**Never commit datasets.** `data/` and `*.csv` are gitignored.

**`services/_shared/` is the only home for cross-service code** — JWT
validation, correlation ids, error envelope, content negotiation, health
endpoints. Do not copy it into individual services.

## Conventions

- Python: PEP 8, `black`, `ruff`. Tables are snake_case and singular.
- Commits: Conventional Commits, `type(scope): subject (#issue)`.
  Example: `feat(auth): add refresh token rotation (#42)`
- Branches: `feature/<issue-number>-kebab-slug`, also `fix/`, `chore/`, `docs/`.
- `main` is protected. Work merges into `develop` via reviewed pull request.

## Before reporting a task complete

1. `pytest` passes.
2. `black --check .` and `ruff check .` pass.
3. Any schema change has a migration.
4. New config documented in `.env.example`.
5. The Definition of Done in `docs/scrum/00-scrum-framework-charter.md` §6 is met.

## Where decisions live

Architectural decisions go in `docs/adr/` as numbered ADRs. ADRs are immutable
once accepted — supersede, never edit. Do not make an architectural decision
silently in code; write the ADR or ask.

# Documentation Index

## Process

| Document | Purpose |
|---|---|
| [Scrum Framework Charter](scrum/00-scrum-framework-charter.md) | Roles, cadence, ceremonies, Definition of Done, capacity model |
| [Product Backlog — Epics](scrum/01-product-backlog-epics.md) | 34 epics across all four components |
| [Release Plan and Sprint Calendar](scrum/02-release-plan-and-sprint-calendar.md) | Semester work plan, sprint goals, deliverable coverage |
| [Sprint 0 Backlog](scrum/03-sprint-00-backlog.md) | Foundation stories with acceptance criteria |
| [Risk Register](scrum/04-risk-register.md) | Exposure matrix and mitigations |

## Architecture decisions

Numbered, immutable once accepted. Supersede rather than edit.

| ADR | Subject |
|---|---|
| ADR-001 | Authentication and token lifecycle |
| ADR-002 | Data ownership map |
| ADR-003 | API standards and content negotiation |
| ADR-004 | Deferring Kubernetes |

## Design

| Area | Location |
|---|---|
| Problem analysis | [`analysis/`](analysis/) |
| Requirements, stories, business rules | [`requirements/`](requirements/) |
| Diagrams | [`architecture/`](architecture/) |
| PostgreSQL, MongoDB, Redis | [`data/`](data/) |
| Permission matrix | [`security/`](security/) |
| Interface prototypes | [`ux/`](ux/) |
| Demo script | [`demo/`](demo/) |

## Data model (S0-04)

| Artifact | Location |
|---|---|
| PostgreSQL data model — design document | [`data/postgresql-model.md`](data/postgresql-model.md) |
| PostgreSQL physical model — generated Mermaid | [`architecture/postgresql-physical-model.md`](architecture/postgresql-physical-model.md) |
| Physical model — authoritative DDL | [`../infra/sql/schema/001_m1_initial_schema.sql`](../infra/sql/schema/001_m1_initial_schema.sql) |
| Schema verification script | [`../infra/sql/schema/verify_m1_schema.sql`](../infra/sql/schema/verify_m1_schema.sql) |

The Mermaid physical model is the reviewable schema artifact: CI reflects a
database migrated to Alembic `head` and fails if the committed model differs.
The executable DDL remains the source of truth for columns and relationships.

# Backlog

Work for the current delivery, grouped by the scope phases. Story identifiers
are `F<phase>-<n>`.

Estimates are set by the team at Planning, not here. Sprint membership lives in
the GitHub Projects `Sprint` field, not in this document — a backlog that
duplicates the board drifts from it within a week.

Priority: **P0** the delivery fails without it · **P1** required, schedulable
later · **P2** optional.

---

## Phase 0 — Preparation

| ID | Story | Priority | Depends on |
|---|---|---|---|
| F0-01 | Reset the repository, documentation and board to the monolith scope | P0 | — |
| F0-02 | Decide the company name and the design system | P0 | — |
| F0-03 | Every member has a GCP account, the SDK CLI, an editor and Git working | P0 | — |
| F0-04 | Confirm the host assignment and whether the team delivers once (Q-1, Q-2) | P0 | — |

## Phase 1 — GCP infrastructure

| ID | Story | Priority | Depends on |
|---|---|---|---|
| F1-01 | Create the Compute Engine instance: e2-standard-2, 50 GB persistent disk, CentOS 10 Stream | P0 | F0-03 |
| F1-02 | Firewall rules allowing HTTP/80 and SSH/22; SSH access verified by every member | P0 | F1-01 |
| F1-03 | Install PostgreSQL from the official repository on the instance | P0 | F1-02 |
| F1-04 | Configure `postgresql.conf` and `pg_hba.conf` for remote access by the application role | P0 | F1-03 |
| F1-05 | Create the application database role with least privilege, separate from the owner | P0 | F1-04 |

## Phase 2 — Database

| ID | Story | Priority | Depends on |
|---|---|---|---|
| F2-01 | Conceptual model: entities, attributes, relationships, functional and multivalued dependencies | P0 | F0-02 |
| F2-02 | Normalize to 4NF, with a written justification for each decision | P0 | F2-01 |
| F2-03 | Logical model: ER diagram and data dictionary | P0 | F2-02 |
| F2-04 | `sql/00_create_database.sql` | P0 | F2-03 |
| F2-05 | `sql/01_schema.sql` with primary keys, foreign keys, `UNIQUE`, `CHECK` and indexes | P0 | F2-04 |
| F2-06 | `sql/02_seed_30_per_table.sql`, at least 30 rows per table | P0 | F2-05 |
| F2-07 | Integrity verification: positive and negative cases, with evidence | P0 | F2-06 |

**F2-02 is the phase's real deliverable.** The scripts are mechanical once the
model is right; a model that is wrong is discovered during Phase 3 and costs the
application layer that was built on top of it.

## Phase 3 — Application

| ID | Story | Priority | Depends on |
|---|---|---|---|
| F3-01 | Flask application skeleton organized by layers | P0 | F0-03 |
| F3-02 | Database connection driven by environment variables | P0 | F3-01, F1-05 |
| F3-03 | Authentication: login, logout, password hashing | P0 | F3-02, F2-05 |
| F3-04 | Administrator module: complete CRUD over every catalog | P0 | F3-03 |
| F3-05 | Regular user module: listing, search and detail views | P0 | F3-03 |
| F3-06 | User management: create, edit, deactivate | P0 | F3-04 |
| F3-07 | Image handling: upload JPG/PNG/WebP, store under `uploads/`, keep the path in the database | P0 | F3-04 |
| F3-08 | Apply the chosen design system across the interface | P1 | F0-02, F3-04 |

## Phase 4 — Security and roles

| ID | Story | Priority | Depends on |
|---|---|---|---|
| F4-01 | Roles and permissions with an authorization middleware enforcing them at the route level | P0 | F3-03 |
| F4-02 | Single administrator: refused by the application **and** by a partial unique index | P0 | F4-01 |
| F4-03 | Input validation and sanitization; every SQL statement parameterized | P0 | F3-04 |
| F4-04 | Secure sessions; no secrets in source, all configuration in `.env` | P0 | F3-02 |
| F4-05 | Controlled error handling and basic application logging | P1 | F3-01 |

**F4-02 needs both halves.** The application check alone is bypassed by a direct
`INSERT`; the index alone produces an unexplained database error in the user
interface. Neither is sufficient by itself.

## Phase 5 — Testing and quality

| ID | Story | Priority | Depends on |
|---|---|---|---|
| F5-01 | Functional tests over registration, login, each CRUD module and image upload | P0 | Phase 3 |
| F5-02 | Negative tests: unauthorized access, invalid data, controlled errors | P0 | Phase 4 |
| F5-03 | Code review pass over consistency, error handling and logging | P1 | F5-01 |

## Phase 6 — Deployment and publication

| ID | Story | Priority | Depends on |
|---|---|---|---|
| F6-01 | NGINX or Apache configured as a reverse proxy in front of the application | P0 | F1-02 |
| F6-02 | Gunicorn under `systemd`, restarting automatically | P0 | F6-01, F3-01 |
| F6-03 | SSL certificate with forced HTTPS | P2 | F6-01 |
| F6-04 | Publish the documentation and evidence page on the assigned host | P0 | F0-04 |
| F6-05 | Final verification: links, images, downloads, checked in a private window | P0 | F6-04 |

**Deploy early.** F6-01 and F6-02 depend on almost nothing and are scheduled as
soon as the application serves a single page. A first deployment attempted near
the delivery date is the most common way this kind of project fails.

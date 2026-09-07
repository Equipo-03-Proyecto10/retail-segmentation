# Scope — First delivery

Monolithic web application for retail segmentation management. This document is
the agreed boundary of the current delivery: what is included, what is not, and
what a finished delivery looks like.

Anything not listed here is out of scope. Adding to it is a decision, and a
decision goes in [`adr/`](adr/).

---

## 1. Objective

Build and deploy a single monolithic web application that manages the retail
domain, using PostgreSQL as the database engine, with authentication, complete
CRUD, image handling, and publication on the assigned host.

The segmentation analytics that give the project its name — RFM, clustering,
segment migration — are **not** part of this delivery. They are later modules of
the same application; see [`roadmap.md`](roadmap.md).

## 2. Constraints

| # | Constraint |
|---|---|
| C-1 | No external REST / GraphQL / SOAP APIs |
| C-2 | No JSON or XML as the exchange format between internal components |
| C-3 | One single application (monolithic) |
| C-4 | Single administrator user |
| C-5 | Everything runs on the team's own GCP instance |
| C-6 | No managed cloud database services (Cloud SQL, AlloyDB, and equivalents) |
| C-7 | No microservices |
| C-8 | All work published on the assigned host |

C-2 is satisfied by server-side rendering: the browser receives HTML, not a
JSON payload consumed by a client-side framework.

C-5 and C-8 name the same machine: the assigned host **is** the team's GCP
instance, not a separate publishing target. See §8, Q-2.

## 3. Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12 |
| Web framework | Flask + Jinja2 |
| Database | PostgreSQL, installed on the VM |
| Web server | NGINX or Apache as reverse proxy |
| Process manager | systemd + Gunicorn |
| Operating system | CentOS 10 Stream |
| Instance | GCP Compute Engine, e2-standard-2 (2 vCPU, 8 GB), 50 GB persistent disk |

The exercise statement illustrates the stack with Node.js + Express. We use
Flask for the same architecture; the reasoning is recorded in
[ADR-0001](adr/0001-flask-monolith-on-a-single-vm.md). Everything else in the
statement — PostgreSQL, Compute Engine, CentOS, the monolith, the constraints —
applies unchanged.

## 4. Phases

| Phase | Name | Contents |
|---|---|---|
| 0 | Preparation | Understand the problem and scope; work plan; tooling (GCP account, SDK, editor, Git) |
| 1 | GCP infrastructure | Install GCP SDK CLI; create the Compute Engine instance; SSH access; install PostgreSQL; configure `postgresql.conf` and `pg_hba.conf`; enable remote access for the application role |
| 2 | Database | Conceptual design (entities, attributes, relationships, dependencies); normalization to 4NF with a justification per decision; logical design (ER diagram + data dictionary); SQL scripts; integrity verification |
| 3 | Application | Python environment; dependencies; layered project structure; database connection through environment variables; administrator module (complete CRUD); user module; user management; image handling |
| 4 | Security and roles | Roles and permissions (regular user, administrator, authorization middleware); single administrator enforced in the application *and* by a partial unique index; input validation and sanitization, parameterized SQL, environment variables, secure sessions |
| 5 | Testing and quality | Functional tests; negative tests (unauthorized access, invalid data, controlled errors); code review |
| 6 | Deployment and publication | Web server as reverse proxy; run under a process manager with automatic restart; SSL (optional); publish to the assigned host; final verification |

Phases are an ordering of the work, not a schedule. Sprint boundaries are set in
[`backlog.md`](backlog.md).

## 5. Deliverables

| # | Deliverable | Location |
|---|---|---|
| 1 | Functional and non-functional requirements | `docs/requirements.md` |
| 1a | User stories | `docs/user-stories.md` |
| 1b | Business rules | `docs/business-rules.md` |
| 1c | Profile and permission matrix | `docs/requirements.md` §3 |
| 2 | ER model in 4NF and data dictionary | `docs/data-model.md` |
| 3 | Justification of design decisions | `docs/adr/` |
| 4 | `00_create_database.sql` | `sql/` |
| 5 | `01_schema.sql` | `sql/` |
| 6 | `02_seed_30_per_table.sql` — at least 30 rows per table | `sql/` |
| 7 | Integrity test evidence | `docs/evidence/` |
| 8 | Complete project under Git, organized by layers | repository |
| 9 | `README.md` with instructions to run it | repository root |
| 10 | Screenshots of the key functionality | `docs/evidence/` |
| 11 | Negative test cases and their results | `docs/evidence/` |
| 12 | Application running in the cloud | assigned host |
| 13 | Proof of deployment | `docs/evidence/` |
| 14 | Web page carrying all documentation and evidence | assigned host |

Directories that do not exist yet are created by the story that produces the
first file in them. Empty directories are not committed.

## 6. Out of scope

- External APIs (REST, GraphQL, SOAP)
- JSON or XML exchange between internal components
- Microservices — deferred to the second delivery, see [`roadmap.md`](roadmap.md)
- Managed cloud database services
- RFM, clustering, segment migration, dashboards — deferred, see [`roadmap.md`](roadmap.md)
- Mobile and desktop clients — the desktop client is deferred, see
  [`roadmap.md`](roadmap.md); the mobile client is not scheduled
- Kubernetes

## 7. Success criteria

The delivery is complete when all five hold:

1. The application is functional and complete.
2. The database is normalized to 4NF.
3. Security and roles behave correctly, including the single-administrator rule.
4. The deployment is correct and stable.
5. Documentation and evidence are complete.

## 8. Open questions

These need an answer from the Product Owner. Work does not stop waiting for
them; when one is answered, it moves out of this section.

| ID | Question | Working assumption |
|---|---|---|
| Q-5 | The first-partial delivery document lists "Diseño de MongoDB" and "Diseño de Redis" among its deliverables. This document and [ADR-0001](adr/0001-flask-monolith-on-a-single-vm.md) retired both, and the delivery has one database engine. Which is binding? | The scope recorded here is binding. Both designs are committed as designs only, with nothing installed — [ADR-0005](adr/0005-document-mongodb-and-redis-designs-without-implementing-them.md) |

Q-1 (how many times the team delivers) and Q-2 (which host is assigned) are
resolved, and they resolve together: **the team delivers once, and the assigned
host is the team's own GCP instance** — the Compute Engine VM in project
`iac-dev-01`, reached at `34.51.123.31` (`docs/infra.md`). C-5 and C-8 therefore
name the same machine. `https://ubiquitous.udem.edu/~iac-<matricula>`, the
working assumption both questions carried, is not part of the delivery: a
per-user directory on a shared host cannot run `systemd`, `gunicorn` or
PostgreSQL, so publishing there would have contradicted C-5 and C-6.

This settles *which* machine, and one consequence outlived the question for a
while: the instance has no DNS name, and Let's Encrypt does not issue for a bare
IP, so F6-03 (#79) sat on Path B — a self-signed certificate that makes browsers
warn. That is now resolved as the deployment task it always was. The delivery is
published at **`mosaiq.maxthecoder.online`**, a subdomain of a domain a team
member owns, proxied through Cloudflare; browsers get a valid certificate and
F6-03's acceptance criterion is genuinely met. The decision, and the cost of
depending on one member's domain, are in
[ADR-0013](adr/0013-publish-mosaiq-through-cloudflare-with-an-origin-certificate.md)
and [`deploy/README.md`](../deploy/README.md).

Q-3 (company name and brand identity) and Q-4 (design system for the
interface) are resolved: the product is **MOSAIQ**, with the design system
recorded in [ADR-0002](adr/0002-mosaiq-identity-and-design-system.md) and
committed under [`docs/design-system/`](design-system/).

The delivery document's third retired item — container execution — is also
resolved, and did not need the Product Owner. The application runs under both
systemd and Docker Compose, systemd remaining the default on the instance:
[ADR-0006](adr/0006-run-under-both-systemd-and-docker-compose.md), built by
F3-09 (#89).

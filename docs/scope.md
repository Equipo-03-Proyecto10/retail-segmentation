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
- Microservices
- Managed cloud database services
- RFM, clustering, segment migration, dashboards — deferred, see [`roadmap.md`](roadmap.md)
- Mobile and desktop clients
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
| Q-1 | The statement says the work is individual, but this is a registered team project. Does the team deliver once, or does each member deliver their own instance? | The team delivers once, on one shared instance |
| Q-2 | Which host URL is assigned, given Q-1? | `https://ubiquitous.udem.edu/~iac-<matricula>` of one designated member |

Q-3 (company name and brand identity) and Q-4 (design system for the
interface) are resolved: the product is **MOSAIQ**, with the design system
recorded in [ADR-0002](adr/0002-mosaiq-identity-and-design-system.md) and
committed under [`docs/design-system/`](design-system/).

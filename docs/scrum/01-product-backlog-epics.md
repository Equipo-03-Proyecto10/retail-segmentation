# Product Backlog — Epic Level

**Project:** Dynamic Segmentation and Retail Personalization Platform
**Backlog owner:** Raquel (Proxy Product Owner)
**Version:** 1.0
**Date:** 2026-08-08

This backlog is deliberately coarse beyond Milestone 1. Epics targeted at M2 and M3 are recorded so the semester plan is visible, but they are not decomposed into stories. Writing detailed acceptance criteria in August for work that starts in October is waterfall planning wearing a Scrum label, and the estimates would be wrong.

Only epics tagged **M1** are decomposed into stories, and only for the sprint currently being planned.

---

## Priority scheme

| Priority | Meaning |
|---|---|
| P0 | Milestone deliverable. The milestone fails without it. |
| P1 | Required by the course specification, deferrable across milestones. |
| P2 | Required by the specification but low risk and low effort; scheduled opportunistically. |
| P3 | Optional enhancement. Built only if capacity remains. |

MoSCoW classification is applied per milestone in `02-release-plan-and-sprint-calendar.md`, not here.

---

## Epic index

| ID | Epic | Component | Priority | Target | Depends on |
|---|---|---|---|---|---|
| E-01 | Identity, Authentication and Session Management | Web + Platform | P0 | M1 | — |
| E-02 | Role and Permission Administration | Web | P0 | M1 | E-01 |
| E-03 | Master Data and Catalogs | Web | P0 | M1 | E-01 |
| E-04 | Transaction Ingestion and Validation | Web + ML | P0 | M1 | E-03 |
| E-05 | RFM Computation Engine | ML | P0 | M1 | E-04 |
| E-06 | Segmentation Engine — Batch Clustering | ML | P0 | M1 | E-05 |
| E-07 | Segment Migration and Historical Traceability | ML + Web | P0 | M1 | E-06 |
| E-08 | Audit Log and Compliance Trail | Web + Platform | P0 | M1 | E-01 |
| E-09 | Dashboards and Highcharts Visualization | Web | P0 | M1 | E-06 |
| E-10 | Public Site and Loyalty Enrollment | Web | P1 | M1 | — |
| E-11 | Local Container Infrastructure | Infrastructure | P0 | M1 | — |
| E-12 | GCP Deployment — Compute Engine | Infrastructure | P1 | M1 | E-11 |
| E-13 | File and Evidence Management (Cloud Storage) | Web + Platform | P1 | M1 | E-01 |
| E-14 | System Configuration and Business Rules | Web | P1 | M2 | E-02 |
| E-15 | Notifications | Web + Mobile | P1 | M2 | E-01 |
| E-16 | Reporting and Export | Web + Desktop | P1 | M2 | E-09 |
| E-17 | Microservices Platform Foundation | Microservices | P0 | M2 | E-01, E-11 |
| E-18 | Incremental Segmentation Service | Microservices + ML | P0 | M2 | E-06, E-17 |
| E-19 | Online Clustering Service | Microservices + ML | P0 | M2 | E-18 |
| E-20 | Concept Drift Detection Service | Microservices + ML | P0 | M2 | E-18 |
| E-21 | Recommendation Engine — Collaborative Filtering | Microservices + ML | P0 | M2 | E-04, E-17 |
| E-22 | Recommendation Engine — Content-Based | Microservices + ML | P1 | M2 | E-03, E-17 |
| E-23 | Inventory Availability Linkage | Microservices | P1 | M2 | E-03, E-17 |
| E-24 | Campaign Management | Web + Microservices | P1 | M2 | E-07, E-17 |
| E-25 | A/B Experimentation and Control Groups | Microservices + ML | P0 | M3 | E-24 |
| E-26 | Conversion, Uplift and Statistical Testing | Microservices + ML | P0 | M3 | E-25 |
| E-27 | Model Comparison | Microservices + ML | P1 | M3 | E-06, E-19 |
| E-28 | Service Health Monitoring Dashboard | Microservices | P0 | M2 | E-17 |
| E-29 | Android Consumer Application | Mobile | P0 | M3 | E-17 |
| E-30 | Android Store Manager Profile | Mobile | P2 | M3 | E-29 |
| E-31 | Desktop Analyst Application (XML) | Desktop | P0 | M3 | E-17 |
| E-32 | Observability, Logging and Load Testing | Platform | P0 | M3 | E-17 |
| E-33 | GKE Cluster Migration | Infrastructure | P3 | M3 | E-17, E-12 |
| E-34 | Data Privacy and Consent Management | Web + Mobile | P1 | M2 | E-01 |

---

## Milestone 1 epics — decomposed

### E-01 — Identity, Authentication and Session Management
**Component:** Web + Platform · **Priority:** P0 · **Target:** M1

Authentication is designed once and inherited by all four components. The design decision is recorded in ADR-001 before any implementation begins.

The specification requires JWT authentication, but the web system is server-rendered with Jinja2 templates. The team must not build two parallel authentication mechanisms (Flask session cookies for the web plus JWT for the API clients). The resolution: a single JWT scheme, transported in an `HttpOnly` `Secure` `SameSite=Lax` cookie for the web system and in an `Authorization: Bearer` header for the mobile and desktop clients. Redis holds the session record and the revocation list for both transports.

**Scope**
- Access and refresh token issuance, with claim structure fixed in ADR-001
- Refresh token rotation
- Logout with token revocation through a Redis denylist
- Password hashing (Argon2id)
- Password recovery by single-use expiring token
- Login attempt throttling using Redis counters
- Session records in Redis keyed by `session:{user_id}:{jti}`

**Out of scope for M1:** multi-factor authentication, OAuth federation, device registration (M3, mobile).

**Key acceptance signals**
- A revoked token is rejected on the next request without a database query
- An expired access token is transparently renewed by a valid refresh token
- A password reset link cannot be reused

---

### E-02 — Role and Permission Administration
**Component:** Web · **Priority:** P0 · **Target:** M1

Seven profiles are specified: Administrator, Commercial Analyst, Store Manager, Marketing, Inventory Planner, Auditor, and Public/Customer.

All seven roles exist in the database with a complete, documented permission matrix in M1. Only three receive real screens and differentiated menus in M1: **Administrator**, **Commercial Analyst**, and **Auditor**. The remaining four authenticate successfully, see their correct menu structure, and land on a placeholder dashboard.

This split is deliberate. Building seven differentiated user experiences inside a 212-hour budget would leave no capacity for the segmentation pipeline, which is the actual subject of the project. The permission matrix is a documentation deliverable and is cheap; the screens are not.

Auditor is included in M1 because it is nearly free — a read-only view over the audit log — and it is the profile that best demonstrates the traceability requirement to an evaluator.

**Scope**
- `role`, `permission`, `role_permission`, `user_role` tables
- Permission decorator enforcing authorization at the route level
- Menu rendering driven by the permission set, not hard-coded per role
- User administration: create, edit, deactivate, assign roles
- Permission matrix document covering all seven profiles

---

### E-03 — Master Data and Catalogs
**Component:** Web · **Priority:** P0 · **Target:** M1

The course requires at least two functional catalogs. This epic delivers four, because the segmentation pipeline cannot run without them.

**Scope**
- Product catalog with category assignment
- Category catalog (hierarchical, single level in M1)
- Store catalog with region attribute
- Customer registry with consent flags
- Search, pagination, soft delete, and audit trail on all four

---

### E-04 — Transaction Ingestion and Validation
**Component:** Web + ML · **Priority:** P0 · **Target:** M1

RFM analysis over 20 hand-entered rows produces meaningless quintiles and no observable migrations. The demonstration requires a realistic transaction history spanning at least 18 months.

**Scope**
- CSV upload through the web interface, stored in Cloud Storage
- Row-level validation with a rejection report
- Ingestion into `sales_transaction` and `sales_transaction_line`
- Ingestion telemetry, parse errors, and per-file summaries written to MongoDB as variable-shape documents
- Seed loader for the base dataset (see `docs/data/seed-strategy.md`)
- Synthetic store, channel, and migration overlay applied to the base dataset

**Note on MongoDB justification.** Ingestion runs produce documents of genuinely variable shape: differing error types, differing column mappings per source file, nested per-row rejection detail. This is a defensible reason for a document store rather than a decorative one, and the justification is required by the course deliverables.

---

### E-05 — RFM Computation Engine
**Component:** ML · **Priority:** P0 · **Target:** M1

**Scope**
- Recency, Frequency and Monetary computation per customer over a configurable analysis window
- Quintile scoring (1–5 per dimension) with configurable boundary strategy
- Immutable RFM snapshot per computation run
- Deterministic recomputation: the same input window and parameters produce the same output

**Design constraint:** RFM snapshots are never overwritten. Each run writes a new snapshot linked to a `segmentation_model_run`. Migration detection depends on this.

---

### E-06 — Segmentation Engine — Batch Clustering
**Component:** ML · **Priority:** P0 · **Target:** M1

M1 delivers batch K-means only. Incremental K-means (E-18) and online clustering (E-19) are M2. Attempting streaming clustering before the batch version and the traceability model are proven is the fastest route to an unexplainable model.

**Scope**
- Feature assembly from RFM snapshot plus category-mix features
- Feature scaling, persisted with the run so it can be reapplied
- K-means with configurable `k`
- Cluster quality metrics recorded per run (silhouette, inertia)
- Human-readable segment labelling
- Full run parameters, seed, and metrics stored in `segmentation_model_run`

---

### E-07 — Segment Migration and Historical Traceability
**Component:** ML + Web · **Priority:** P0 · **Target:** M1

This epic addresses the central requirement of the problem statement: segments must be updated without losing historical traceability. It is a data modeling problem before it is a machine learning problem, and getting it wrong makes the stated objective unachievable.

**The modeling rule:** a customer's segment assignment is never updated in place. There is no mutable `customer.segment_id` column. Each segmentation run closes the previous assignment by setting `valid_to` and inserts a new row.

**Core tables**
| Table | Purpose |
|---|---|
| `segmentation_model_run` | One row per algorithm execution: version, parameters, `k`, seed, quality metrics, triggering user, timestamp |
| `segment` | Segment definitions belonging to a specific run, not global |
| `customer_rfm_snapshot` | Raw R, F, M values and scores per customer per run |
| `customer_segment_assignment` | `customer_id`, `segment_id`, `model_run_id`, `valid_from`, `valid_to` (nullable), `rfm_snapshot_id` |

**Consequences**
- Migration detection becomes a query comparing two consecutive assignments for a customer, not a separate subsystem
- An auditor can reconstruct any customer's segment as of any past date
- Model comparison (E-27) becomes possible because two runs can coexist over the same population

**Scope for M1**
- Bi-temporal schema, frozen in Sprint 0
- Assignment closure and insertion logic
- Migration query and a migration report view
- Point-in-time segment lookup for the Auditor profile

---

### E-08 — Audit Log and Compliance Trail
**Component:** Web + Platform · **Priority:** P0 · **Target:** M1

**Scope**
- Append-only `audit_log` table: actor, action, entity type, entity id, before/after payload, IP, timestamp, correlation id
- Automatic capture on state-changing operations rather than per-route manual calls
- Auditor read-only search interface with filters
- Structured application event and error logging through `structlog`

---

### E-09 — Dashboards and Highcharts Visualization
**Component:** Web · **Priority:** P0 · **Target:** M1

**Scope**
- Segment size distribution
- RFM score distribution
- Migration flow between two runs
- Revenue by segment
- Chart data served from dedicated JSON endpoints, cached in Redis with explicit invalidation on new runs

---

### E-10 — Public Site and Loyalty Enrollment
**Component:** Web · **Priority:** P1 · **Target:** M1

**Scope**
- Public landing page, product catalog browsing, general promotions
- Loyalty program enrollment form with explicit consent capture
- Responsive layout

---

### E-11 — Local Container Infrastructure
**Component:** Infrastructure · **Priority:** P0 · **Target:** M1

**Scope**
- `Dockerfile` for the web application on a `python:3.12-slim` base
- `docker-compose.yml` with PostgreSQL 16, MongoDB 7, Redis 7, and the web service
- Named volumes, health checks, and dependency ordering
- `.env.example` with every required variable
- One-command bootstrap: up, migrate, seed

**Base image note:** Alpine is rejected. The scientific Python wheels required by scikit-learn and pandas are not available for musl, forcing source compilation and long build times.

---

### E-12 — GCP Deployment — Compute Engine
**Component:** Infrastructure · **Priority:** P1 · **Target:** M1

**Scope**
- Compute Engine instance running the containerized stack
- VPC, private subnet, firewall rules restricting database ports to internal traffic
- Secrets held in Secret Manager, not in environment files on the instance
- Cloud Storage buckets for uploads and evidence, with lifecycle rules

---

### E-13 — File and Evidence Management
**Component:** Web + Platform · **Priority:** P1 · **Target:** M1

**Scope**
- Cloud Storage upload and retrieval abstraction with a local filesystem fallback for development
- Signed URLs for private object access
- File metadata registry in PostgreSQL, file-level processing detail in MongoDB
- MIME and size validation

---

## Epics deferred beyond Milestone 1

Recorded for planning visibility. Not decomposed. Estimates are intentionally absent.

| ID | Epic | Note on why it is deferred |
|---|---|---|
| E-14 | System Configuration and Business Rules | Depends on knowing which rules actually vary in practice, which becomes clear only after the batch pipeline runs |
| E-15 | Notifications | No consumer until the mobile app exists |
| E-16 | Reporting and Export | Desktop app is the primary consumer; XML contract must exist first |
| E-17 | Microservices Platform Foundation | API versioning, JSON and XML content negotiation, rate limiting, correlation ids, health endpoints, OpenAPI. The `Accept`-header contract is designed in M1 (ADR-003) even though implementation is M2, because retrofitting XML later means rewriting the serialization layer. |
| E-18 | Incremental Segmentation Service | Requires the batch version and the traceability schema to be proven first |
| E-19 | Online Clustering Service | Requires E-18 |
| E-20 | Concept Drift Detection Service | Requires a stable feature pipeline and at least two comparable runs |
| E-21 | Recommendation Engine — Collaborative Filtering | Requires transaction volume and the microservices platform |
| E-22 | Recommendation Engine — Content-Based | Requires a populated product attribute model |
| E-23 | Inventory Availability Linkage | Requires the inventory model and the microservices platform |
| E-24 | Campaign Management | Requires segments to be stable and queryable by version |
| E-25 | A/B Experimentation and Control Groups | Requires campaigns |
| E-26 | Conversion, Uplift and Statistical Testing | Requires experiments to have run long enough to produce data |
| E-27 | Model Comparison | Enabled by the run-scoped segment model built in M1 |
| E-28 | Service Health Monitoring Dashboard | Requires services to monitor |
| E-29 | Android Consumer Application | Consumes JSON from the microservices module only |
| E-30 | Android Store Manager Profile | Extension of E-29 |
| E-31 | Desktop Analyst Application | Consumes XML from the microservices module only |
| E-32 | Observability, Logging and Load Testing | Locust load testing requires deployed services |
| E-33 | GKE Cluster Migration | Optional. Justified only if the incremental and drift services demonstrably need independent autoscaling. Not justified by the phrase "online clustering", which describes an algorithm, not an orchestrator. |
| E-34 | Data Privacy and Consent Management | Consent capture begins in M1 (E-10); preference and privacy control surfaces are M2/M3 |

---

## Revision history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-08-08 | Raquel | Initial epic backlog |

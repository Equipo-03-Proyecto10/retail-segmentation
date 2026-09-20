# Scope — Delivery 2, analytics phase

This document scopes only the monolith analytics phase of the second delivery.
It is not the scope of the whole delivery.
[ADR-0016](adr/0016-the-second-delivery-reinstates-the-distributed-architecture.md)
governs that delivery. Its distributed components receive their own scope when
the ADRs that define their contracts exist.

---

## 1. Objective

Expand the existing MOSAIQ platform with the complete dynamic segmentation
capability: traceable assignments, sales ingestion, RFM and K-means modelling,
recommendations, campaigns, experiments and analytics dashboards.

The existing platform is expanded, not rebuilt. Its administrative CRUD,
authentication, authorization, deployment and consultation surfaces remain in
place. This phase adds to the Flask application that already works.

## 2. Relationship to Delivery 2 and to the first delivery

[ADR-0016](adr/0016-the-second-delivery-reinstates-the-distributed-architecture.md)
is the architecture record for the second delivery. This phase is the
monolith's analytics part of that delivery. It does not implement or specify
the distributed part.

The first delivery is complete. [`scope.md`](scope.md) remains its boundary.
It keeps the title "Scope — First delivery". Nothing in this document edits
or reinterprets that completed scope.

## 3. Constraints

| # | Application to this phase |
|---|---|
| C-1 | Does not bind Delivery 2. This phase nevertheless adds no external API and no HTTP ingestion endpoint; sales enter through CSV under [ADR-0020](adr/0020-csv-is-the-sole-sales-ingestion-entry-point-for-this-delivery.md) |
| C-2 | Does not bind Delivery 2. This phase defines no JSON or XML integration contract; its user-facing pages are server-rendered HTML, including the Highcharts dashboards |
| C-3 | Does not bind the second delivery after ADR-0016. This phase changes only the existing Flask application and creates no second deployable unit |
| C-4 | Applies unchanged. There is exactly one `ADMIN`, refused by the application and by the partial unique index; both enforcements remain required |
| C-5 | Applies. Analytics run on the team's own GCP Compute Engine instance |
| C-6 | Applies. PostgreSQL remains installed on the instance; this phase adds no managed database service |
| C-7 | Does not bind the second delivery after ADR-0016. This phase builds no microservice because service boundaries and contracts have no governing ADR yet |
| C-8 | Applies. The completed analytics phase is published on the assigned host as part of the existing web system |

**The absence of distributed work is sequencing, not architecture.** C-1,
C-2, C-3 and C-7 remain the first delivery's boundary. ADR-0016 lifted them
for the second delivery, but requires a specific record before any distributed
component is built. This phase reaches no such decision.

**The administrator rule is not a role-profile limit.** C-4 permits one
administrator account. The existing non-administrative profiles continue to
operate under
[ADR-0007](adr/0007-permissions-in-code-with-a-default-deny-middleware.md).

## 4. Stack additions

Only these additions apply on top of [`scope.md`](scope.md) §3:

| Purpose | Technology | Reason |
|---|---|---|
| K-means fitting | None — implemented in the application | [ADR-0021](adr/0021-k-means-is-implemented-in-the-application-rather-than-taken-as-a-dependency.md) decides the `KMEANS` strategy [ADR-0018](adr/0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md) requires is written in the service layer. `web/requirements.txt` is unchanged |
| Dashboard charts | Highcharts | Renders the required analytics dashboards on server-rendered pages |

No datastore, runtime, web framework or deployment unit is added in this
phase, and no Python package either: the runtime stays at the five in
`web/requirements.txt`. Highcharts is already committed under
[`design-system/charts/`](design-system/charts/) by
[ADR-0002](adr/0002-mosaiq-identity-and-design-system.md), so Phase 12 adopts
an existing choice rather than making a new one.

## 5. Phases

Phases 0 through 6 belong to the first delivery and are complete. The
analytics phase continues with these six phases:

| Phase | Name | Contents |
|---|---|---|
| 7 | Segmentation traceability | Replace the mutable current segment with durable runs, stable labels, assignment history and migration traceability under [ADR-0017](adr/0017-segment-assignment-history-replaces-the-mutable-current-segment.md) and [ADR-0018](adr/0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md) |
| 8 | Sales ingestion and consumption profile | Import the versioned CSV, report row-level acceptance and rejection, and derive customer consumption profiles from accepted sales under [ADR-0020](adr/0020-csv-is-the-sole-sales-ingestion-entry-point-for-this-delivery.md) |
| 9 | Segmentation modelling | Run `RFM_RULES` and `KMEANS`, record their parameters and quality measures, and compare assignments through stable labels under [ADR-0018](adr/0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md) |
| 10 | Recommendations | Produce recommendations from stable assignment labels without reading the producing method, under [ADR-0018](adr/0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md) |
| 11 | Campaigns and experiments | Manage campaigns and keep assignment, exposure and conversion as separate durable events under [ADR-0019](adr/0019-experiment-measurement-separates-assignment-exposure-and-conversion.md) |
| 12 | Analytics | Publish segment, RFM, migration, revenue and experiment dashboards governed by [ADR-0018](adr/0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md) and [ADR-0019](adr/0019-experiment-measurement-separates-assignment-exposure-and-conversion.md) |

Every new surface inherits
[ADR-0007](adr/0007-permissions-in-code-with-a-default-deny-middleware.md).
The roles already exist. Planning defines which existing or new permission
each surface requires; it does not create replacement role profiles.

## 6. Deliverables

| # | Deliverable | Location |
|---|---|---|
| 1 | Analytics requirements, user stories, business rules and permission changes | `docs/requirements.md`, `docs/user-stories.md`, `docs/business-rules.md` |
| 2 | Updated 4NF model, normalization argument and data dictionary | `docs/data-model.md` |
| 3 | Analytics decisions and their review status | `docs/adr/` |
| 4 | Clean-run schema and seed data for every new or changed relation | `sql/01_schema.sql`, `sql/02_seed_30_per_table.sql` |
| 5 | Versioned CSV ingestion with a row-level rejection report | `web/routes/`, `web/services/`, `web/db/` |
| 6 | Durable segmentation runs, history, both modelling strategies and migration reporting | `web/services/`, `web/db/`, `web/templates/` |
| 7 | Label-based recommendations, campaigns and experiment measurement | `web/services/`, `web/db/`, `web/templates/` |
| 8 | Highcharts analytics dashboards rendered by the web application | `web/templates/`, `web/static/` |
| 9 | Functional, negative, contract and deterministic measurement tests | `tests/` |
| 10 | Screenshots, test results and demonstration evidence | `docs/evidence/` |
| 11 | Analytics phase running within the existing web deployment | assigned host |

## 7. Out of scope for this phase

The following remain **in Delivery 2** under ADR-0016, but are **not started**
in this phase:

| Component | Phase status |
|---|---|
| Microservices | Not started; blocked on an ADR defining service boundaries and contracts that does not exist yet |
| Android client | Not started; blocked on its client and JSON contract ADR, which does not exist yet |
| Desktop client | Not started; blocked on its client, XML and XSD contract ADR, which does not exist yet |
| MongoDB | Not started; blocked on a datastore ownership ADR that does not exist yet |
| Redis | Not started; blocked on a session, revocation and operational-use ADR that does not exist yet |
| Shared JWT | Not started; blocked on an issuance, signing, validation and revocation ADR that does not exist yet |

This phase also has no HTTP ingestion endpoint under
[ADR-0020](adr/0020-csv-is-the-sole-sales-ingestion-entry-point-for-this-delivery.md),
no second deployable unit, and no second administrator. A second administrator
is never permitted by C-4; it is not deferred work.

This list is a sequencing statement. It does not retire, weaken or replace the
distributed architecture accepted in ADR-0016.

## 8. Success criteria

The analytics phase is complete when all of these are checkable and pass:

1. The three SQL scripts run clean in order against an empty PostgreSQL, the
   model remains justified in 4NF, and seed counts or documented exemptions
   meet repository policy.
2. A mixed CSV load reports received, accepted and rejected counts that
   reconcile, retains a row number and reason for every rejection, and exposes
   no HTTP ingestion endpoint.
3. Both `RFM_RULES` and `KMEANS` produce atomic, durable runs and assignment
   history. Current-segment reads use the one open history row.
4. Migration, recommendations and dashboards consume stable labels, never raw
   K-means cluster identifiers, and do not branch on the producing method.
5. Experiment assignment, exposure and conversion remain distinct. The A/A and
   injected-uplift validations in ADR-0019 pass, and synthetic results are
   labelled `Synthetic` on every rendered surface.
6. Every new route declares a permission and passes the ADR-0007 default-deny
   checks for all existing roles.
7. Exactly one `ADMIN` remains enforced by the application and by the partial
   unique index.
8. The existing administrative CRUD still works, the analytics pages render at
   375 px and 1440 px, and the assigned host runs the completed phase.
9. The applicable Definition of Done in [`process.md`](process.md) §6 passes,
   including tests, formatting, linting, documentation and evidence.

## 9. Answered by the Product Owner

| ID | Answer |
|---|---|
| Q-6 | **ANSWERED.** Expand the existing platform; do not rebuild it. Keep the administrative CRUD already delivered. |
| Q-7 | **ANSWERED.** Delivery 2's priority is the complete dynamic segmentation capability. |
| Q-8 | **ANSWERED.** C-1 and C-2 bind the monolith as delivered in the first delivery, not the future microservices module. That module may expose JSON and XML. This phase records the exemption but does not act on it; [`roadmap.md`](roadmap.md) reaches the same conclusion through the dashboard requirement. |
| Q-9 | **ANSWERED.** Commercial analyst (`ANALYST`), store manager (`STORE_MANAGER`), marketing (`MARKETING`), inventory planner (`INVENTORY_PLANNER`) and auditor (`AUDITOR`) are in scope and already work under ADR-0007. They are not new work. They are non-administrative profiles. C-4 is unchanged: one `ADMIN`, enforced by both the application and the partial unique index. |

Q-1 through Q-5 remain recorded only in [`scope.md`](scope.md). They are not
renumbered or restated here.

## 10. Open questions

No scope questions remain open.

**Q-10 is answered.** It asked whether K-means arrives as a
scientific-computing dependency such as scikit-learn or as an implementation in
the application. The team decided the application implements it, and the
reasoning — including what the decision costs — is in
[ADR-0021](adr/0021-k-means-is-implemented-in-the-application-rather-than-taken-as-a-dependency.md).
The runtime therefore stays at five packages.

Two things that look like open questions are not. ADR-0018's ordered
segment-label vocabulary is Phase 7 schema and 4NF work that the Phase 9
models consume. ADR-0020's source transaction identifier mapping and duplicate
rule are Phase 8 schema and 4NF work. Neither changes this phase's boundary.

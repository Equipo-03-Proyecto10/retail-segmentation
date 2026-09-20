# Roadmap — second delivery work

Phases 0–6 completed the first delivery. The second delivery is now underway
through the monolith's analytics work, organized as phases 7–12. The
distributed components required by the same delivery have not started.

This file records what is underway, what has not started, and how each kind of
work enters the repository. It is not a schedule. The analytics boundary is in
[`scope-delivery-2-analytics.md`](scope-delivery-2-analytics.md), while
[`scope.md`](scope.md) remains the unchanged first-delivery boundary.

---

## The monolith analytics phase

The modules that this roadmap once deferred are now part of the second
delivery. They extend the existing Flask application. They do not add a
microservice, client application, additional datastore, token format,
ingestion endpoint, or second deployable unit.

| Phase | Name | What it builds | Governing decision |
|---|---|---|---|
| 7 | Segmentation traceability | Durable segmentation runs and segment assignment history in place of the mutable current segment | [ADR-0017](adr/0017-segment-assignment-history-replaces-the-mutable-current-segment.md) |
| 8 | Sales ingestion and consumption profile | Versioned CSV transaction ingestion, rejection reporting and RFM profiles over a configurable window with quintile scoring | [ADR-0020](adr/0020-csv-is-the-sole-sales-ingestion-entry-point-for-this-delivery.md), [ADR-0017](adr/0017-segment-assignment-history-replaces-the-mutable-current-segment.md) |
| 9 | Segmentation modelling | Rule-based RFM and batch K-means behind one pipeline, recorded run parameters and quality measures, and label-based migration reports and matrices | [ADR-0017](adr/0017-segment-assignment-history-replaces-the-mutable-current-segment.md), [ADR-0018](adr/0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md) |
| 10 | Recommendations | Recommendations derived from the same stable, method-agnostic assignment history | [ADR-0017](adr/0017-segment-assignment-history-replaces-the-mutable-current-segment.md), [ADR-0018](adr/0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md) |
| 11 | Campaigns and experiments | Campaign workflows and experiment measurement with separate assignment, exposure and conversion events | [ADR-0019](adr/0019-experiment-measurement-separates-assignment-exposure-and-conversion.md) |
| 12 | Analytics | Server-rendered dashboards for segment sizes, RFM distribution, migration, revenue by segment and experiment results | [ADR-0017](adr/0017-segment-assignment-history-replaces-the-mutable-current-segment.md), [ADR-0018](adr/0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md), [ADR-0019](adr/0019-experiment-measurement-separates-assignment-exposure-and-conversion.md) |

**None of the old deferred-module list remains deferred.** Transaction
ingestion and RFM computation move into Phase 8. Batch clustering and segment
migration move into Phase 9. Segment history moves into Phase 7. Dashboards
move into Phase 12.

**The governing analytics ADRs are still Proposed.** ADR-0017 through
ADR-0020 state the decisions under review. They become binding when accepted,
before the implementation that depends on them merges.

## How work enters

### A module of the monolith

Phases 0 and 1 happen once. The instance, operating system, PostgreSQL and
deployment pipeline already exist, so each analytics module uses the same
short engineering cycle as any other addition to the Flask application:

```
Phase 2 (only if it needs new tables)
  └─> Phase 3  build the module
      └─> Phase 4  declare its required permissions
          └─> Phase 5  functional and negative tests
              └─> Phase 6  deploy and publish
```

These phase numbers name the existing engineering cycle; they do not renumber
the second-delivery phases above. A module that needs new tables amends the
4NF model and [`sql/01_schema.sql`](../sql/01_schema.sql) first. Schema work
keeps one home even when a Phase 7–12 capability triggers it.

The five non-administrative operational profiles already exist: ANALYST,
STORE_MANAGER, MARKETING, INVENTORY_PLANNER and AUDITOR. New screens inherit
the default-deny gate in
[ADR-0007](adr/0007-permissions-in-code-with-a-default-deny-middleware.md).
The new work decides which existing permissions each surface requires; it does
not rebuild the profiles.

Constraint C-4 is unchanged. There is exactly one administrator. A second is
refused by the application and by the partial unique index. Neither
enforcement may be removed.

### A distributed component

A microservice, client application, shared token mechanism or additional
datastore does not enter through the monolith's short cycle. Each adds a new
system boundary or operational dependency.

[ADR-0016](adr/0016-the-second-delivery-reinstates-the-distributed-architecture.md)
reinstates that architecture but deliberately does not choose its boundaries
or contracts. Distributed work re-enters only after the integration-contracts
ADR required by ADR-0016 exists and is accepted. Service boundaries, JWT,
datastore ownership and deployment then need their own accepted decisions
before their implementations begin.

Until those records exist, ADR-0016 remains checkable by absence. The
repository contains no service skeleton, client application, second deployable
unit, MongoDB or Redis connection, shared JWT implementation, or HTTP sales
ingestion endpoint. Phase 8 uses CSV under
[ADR-0020](adr/0020-csv-is-the-sole-sales-ingestion-entry-point-for-this-delivery.md)
without pre-empting a later integration contract.

## The second delivery

[ADR-0016](adr/0016-the-second-delivery-reinstates-the-distributed-architecture.md)
is Accepted. It makes the monolith one component of the distributed second
delivery; it does not replace it. The course scope requires all of the
following:

| Part | Requirement | Current position |
|---|---|---|
| Microservices | Six to ten, each with one responsibility, its own container, versioned routes, authentication, JSON **and** XML responses, a health endpoint and OpenAPI documentation | Not started; boundaries and contracts are undecided |
| Android client | Consumes JSON only. At least four microservices, plus two device capabilities — camera, QR, GPS, notifications, local storage or sensors | Not started; it follows the contracts decision |
| Desktop client | Consumes XML only, validated against an XSD. At least four microservices, and a **different** process from the mobile one | Not started; it follows the contracts decision |
| MongoDB | Real storage, with insert, query, update, aggregation and indexing demonstrated | Not started; ownership and use are undecided |
| Redis | Sessions, revoked tokens, cache, counters and rate limits. Every component consults it during authentication or authorization | Not started; ownership and failure behaviour are undecided |
| Shared JWT | Issued and validated across components, with revocation enforced through Redis | Not started; issuance, signing and revocation are undecided |
| Integration contracts | Specified **before** the clients are built — JSON shapes, HTTP codes, pagination and versioning; XML elements, hierarchy, XSD and namespaces | Not started; this is the first gate for distributed work |
| The web system | Keeps working independently, and grows: the five additional profiles already delivered, advanced administration, filters, pagination, reports, Highcharts, file upload, history and internal notifications | Underway through phases 7–12 |

The end-to-end demonstration remains one process across components. The mobile
client sends JSON, a microservice validates the JWT, Redis confirms that the
session is live, the data lands in PostgreSQL or MongoDB, the web system
displays it, and the desktop client reads it back over XML.

The distributed rows above are not deferred to a later delivery. They are
unstarted parts of this delivery, blocked on the decisions that ADR-0016
requires. The analytics phase can proceed because it grows the independent
monolith without choosing any of those missing contracts.

**ADR-0016 supersedes the former monolith-only boundary.** It reverses
[ADR-0001](adr/0001-flask-monolith-on-a-single-vm.md) and
[ADR-0005](adr/0005-document-mongodb-and-redis-designs-without-implementing-them.md)
for the second delivery. Constraints C-1, C-2, C-3 and C-7 remain the first
delivery's boundary. They neither bind the second delivery nor disappear from
the delivery they governed.

Kubernetes and managed database services remain outside the second delivery.
Nothing in ADR-0016 brings them into scope.

## Two warnings promoted into decisions

The earlier design exposed two failure modes before the analytics work began.
They are now explicit second-delivery decisions rather than reminders for a
later module. ADR-0017 and ADR-0018 are Proposed during planning and must be
accepted before their dependent implementation merges.

**Segment assignments are never updated in place.**
[ADR-0017](adr/0017-segment-assignment-history-replaces-the-mutable-current-segment.md)
replaces the mutable `customer.current_segment_id` with a run and assignment
history. A new run closes the previous open assignment and inserts its
successor, including an unassigned result. A partial unique index prevents a
customer from holding two open assignments at once. Without this history,
every run overwrites the evidence the project needs for trends and migration.

**Migration is a change of segment label, not of segment id.**
[ADR-0018](adr/0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md)
makes stable label codes the output of both rule-based RFM and K-means.
Segments and raw cluster identifiers belong to the run that produced them, so
their identifiers can differ on every run. Comparing those identifiers can
report 100% migration when no customer moved, without raising an error. Every
downstream report compares the stable label code instead.

## The dashboards constraint, resolved by the second delivery

This section used to record an open question for the Product Owner. First
delivery constraint C-2 forbids JSON as the exchange format between internal
components, while charting libraries normally read a JSON endpoint. Whether a
server-rendered page embedding its own chart data counted as an internal
exchange was unanswered.

The second delivery's scope resolves the question. It requires Highcharts in
the web system and JSON between distributed components. C-2 remains a
first-delivery constraint, while the Phase 12 dashboards are second-delivery
work. They therefore do not conflict.

The dashboards still render as HTML from the monolith. That implementation
choice is valid in the second delivery; it is not evidence that C-2 still
binds it or that C-2 has been abolished generally.

## Where the earlier design lives

The previous scope had a fully specified data model, DDL and verification
script for the analytics and distributed design. They were removed when the
scope changed to a monolith. They remain in the Git history at commit
`c4a2b63` under `docs/data/`, `docs/architecture/` and
`infra/sql/schema/`, and are worth reading before the team redesigns the same
tables or contracts.

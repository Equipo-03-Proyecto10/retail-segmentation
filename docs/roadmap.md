# Roadmap — after this delivery

The segmentation analytics are the reason the project exists, but they are not
part of the current delivery. This file records what is deferred and how it
re-enters the plan, so that nothing gets built speculatively now and nothing
gets rediscovered from scratch later.

---

## Deferred modules

| Module | What it adds |
|---|---|
| Transaction ingestion | CSV upload with row-level validation and a rejection report |
| RFM computation | Recency, Frequency and Monetary per customer over a configurable window, with quintile scoring |
| Batch clustering | K-means over the RFM features, with run parameters and quality metrics recorded per run |
| Segment history | Segment assignments kept as history rather than as a mutable column |
| Segment migration | A report of which customers moved between two runs, and in which direction |
| Dashboards | Segment sizes, RFM distribution, migration flow, revenue by segment |

## How a deferred module re-enters

Phases 0 and 1 happen once. The instance, the operating system, PostgreSQL and
the deployment pipeline are already in place after the current delivery, so a
new module starts at **Phase 3** and runs the short cycle:

```
Phase 2 (only if it needs new tables)
  └─> Phase 3  build the module
      └─> Phase 4  define its roles and permissions
          └─> Phase 5  functional and negative tests
              └─> Phase 6  deploy and publish
```

A module that needs new tables amends the 4NF model and `sql/01_schema.sql`
first. Schema work stays in Phase 2 even when it is triggered by a Phase 3
story, so the model keeps a single home.

## The second delivery

The sections above defer *modules of this monolith*. The second delivery is a
different kind of change: it adds deployable units, so none of it re-enters
through the short cycle, and the monolith stops being the system.

Its scope is set by the course and is not the team's to choose. What it requires:

| | |
|---|---|
| Microservices | Six to ten, each with one responsibility, its own container, versioned routes, auth, JSON **and** XML responses, a health endpoint and OpenAPI documentation |
| Android client | Consumes JSON only. At least four microservices, plus two device capabilities — camera, QR, GPS, notifications, local storage, sensors |
| Desktop client | Consumes XML only, validated against an XSD. At least four microservices, and a **different** process from the mobile one |
| MongoDB | Real storage, with insert, query, update, aggregation and indexing demonstrated |
| Redis | Sessions, revoked tokens, cache, counters, rate limits. Every component consults it during authentication or authorization |
| Integration contracts | Specified **before** the clients are built — JSON shapes, HTTP codes, pagination, versioning; XML elements, hierarchy, XSD, namespaces |
| The web system | Keeps working independently, and grows: more profiles, advanced administration, filters, pagination, reports, Highcharts, file upload, history, internal notifications |

The demonstration is one process executed across components: the mobile client
sends JSON, a microservice validates the JWT, Redis confirms the session is
live, the data lands in PostgreSQL or MongoDB, the web system displays it, and
the desktop client reads it back over XML.

**This reverses [ADR-0001](adr/0001-flask-monolith-on-a-single-vm.md) and
[ADR-0005](adr/0005-document-mongodb-and-redis-designs-without-implementing-them.md).**
The first retired the four-component architecture "rather than deferred" and
said deferred work re-enters "as modules of this monolith rather than as
services". The second committed the MongoDB and Redis designs on the condition
that no engine is installed and no connection code is written.
[ADR-0016](adr/0016-the-second-delivery-reinstates-the-distributed-architecture.md)
supersedes both.

ADR-0016 reinstates the architecture and nothing else. It does not decide which
services exist, where their boundaries fall, what the contracts contain, how JWT
is issued and revoked, or which datastore holds what. Each of those is a
decision with its own record, and the scope's own sequencing puts the contracts
first.

**Nothing here is built yet**, and the first delivery's constraints — C-1, C-2,
C-3 and C-7 in [`scope.md`](scope.md) — still hold for everything in this
repository today. That document is titled "Scope — First delivery" and stays
that way; the second delivery needs its own.

The previous four-component design is not a starting point to copy — it was
written for a different product shape — but it answered some of these questions
once and is worth reading before redesigning the same things. It is in the
history at `c4a2b63`; see the last section of this file.

## Two decisions worth carrying forward

These came out of the earlier design work. They are cheap to honour when the
tables are first designed and expensive to retrofit, so they are recorded here
rather than left in the history.

**Segment assignments are never updated in place.** There is no mutable
`customer.segment_id`. A new run closes the previous assignment by setting its
end timestamp and inserts a new row. A `UNIQUE` or exclusion constraint keeps a
customer from holding two open assignments at once. Without this, the history
the project is meant to preserve is overwritten on every run.

**Migration is a change of segment *label*, not of segment id.** Segments belong
to the run that produced them, so a new row exists for every segment on every
run and the id differs every time. A migration report that compares ids reports
100% migration on every run — and raises no error while doing it. Compare the
stable label code instead.

## The dashboards constraint, resolved by the second delivery

This section used to record an open question for the Product Owner. Scope
constraint C-2 forbids JSON as the exchange format between internal components,
charting libraries normally read a JSON endpoint, and whether a server-rendered
page embedding its own chart data counted as an internal exchange was
unanswered.

The second delivery's scope answers it without anyone having to ask: it requires
Highcharts in the web system and JSON between components in the same document.
C-2 is a first-delivery constraint and the dashboards are not first-delivery
work, so the two never actually meet.

The question stands only for anything built under the first delivery's
constraints — which the dashboards are not.

## Where the earlier design lives

The previous scope had a fully specified data model, DDL and verification
script for all of the above. They were removed when the scope changed to a
monolith. They remain in the Git history at commit `c4a2b63` under
`docs/data/`, `docs/architecture/` and `infra/sql/schema/`, and are worth
reading before redesigning the same tables.

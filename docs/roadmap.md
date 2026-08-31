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

## Constraint to resolve before the dashboards

Scope constraint C-2 forbids JSON as the exchange format between internal
components. Charting libraries normally read a JSON endpoint. Whether a
server-rendered page embedding its own chart data counts as an internal exchange
is a question for the Product Owner, and it has to be answered before the
dashboard module is designed, not while it is being built.

## Where the earlier design lives

The previous scope had a fully specified data model, DDL and verification
script for all of the above. They were removed when the scope changed to a
monolith. They remain in the Git history at commit `c4a2b63` under
`docs/data/`, `docs/architecture/` and `infra/sql/schema/`, and are worth
reading before redesigning the same tables.

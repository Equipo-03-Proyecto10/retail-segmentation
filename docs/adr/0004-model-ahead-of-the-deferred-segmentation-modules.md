# ADR-0004 — The PostgreSQL model carries the deferred segmentation tables now

**Status:** Accepted
**Owner:** Marcelo
**Issue:** #58 (F2-05)
**Supersedes:** —
**Superseded by:** —

---

## Context

[`scope.md`](../scope.md) §6 defers RFM, clustering, segment migration and
dashboards to a later delivery, and [`roadmap.md`](../roadmap.md) opens with the
instruction that "nothing gets built speculatively now". Six tables in
[`sql/01_schema.sql`](../../sql/01_schema.sql) belong to exactly those deferred
modules: `segment`, `segment_rule`, `campaign`, `experiment`,
`experiment_group`, `experiment_group_customer`.

They are there because the 4NF model was designed as a whole, against the whole
business domain, before the scope was narrowed. Removing them from the schema
now would mean removing them from the ER diagram, the data dictionary and the
normalization argument that make up the graded Phase 2 deliverable — a model
that stops mid-domain is harder to defend than one that is complete.

Against that, `roadmap.md` records a specific and expensive trap: segment
assignments must be held as history, never updated in place, and
`customer.current_segment_id` is precisely the mutable column it warns about.
Keeping the tables means shipping that column knowing it is wrong.

What is genuinely uncertain is whether the segmentation module, when it is
built, will want these tables as they stand or will redesign them against real
data. Modelling ahead is a bet that the domain is understood well enough now.

## Decision

The six segmentation, campaign and experiment tables stay in
`sql/01_schema.sql` and in the data model documentation, seeded like every
other table. No application code reads or writes them in this delivery: they
carry no routes, no services and no queries, and the modules that will use them
stay deferred. `customer.current_segment_id` ships as a documented known
limitation, replaced by an assignment table with validity dates when the
segment-history module lands.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Trim the schema to the thirteen in-scope tables | The 4NF justification is the graded deliverable, and it argues about the whole domain. Cutting the model to match the current sprint would mean rewriting the argument, and rewriting it again when the tables return |
| Keep the tables and also build the modules | Straightforwardly out of scope, and there are three days to the delivery |
| Keep the tables but drop `current_segment_id` | The column is what makes "a customer belongs to a segment" demonstrable at all in this delivery. Dropping it removes a working relationship to avoid a future migration that is already planned and documented |

## Consequences

**What this makes easy.** The Phase 2 deliverable is a complete, defensible 4NF
model of the business rather than of one sprint. The deferred modules start at
Phase 3 with their tables already designed, reviewed and seeded, exactly as
`roadmap.md` describes the short cycle.

**What this makes hard.** Six tables exist that nothing reads, and dead schema
invites someone to write code against it early. `current_segment_id` is a known
wrong shape shipping on purpose, and whoever builds segment history has to
migrate it — the roadmap is explicit that this retrofit is the expensive kind.
The seed also carries 30 rows in each of those tables, which is 180 rows of
data no feature consumes.

**What must now be true elsewhere.** The segment-history module must replace
`customer.current_segment_id` with an assignment table rather than building on
it — [`roadmap.md`](../roadmap.md) states the constraint and
[`data-model.md`](../data-model.md) repeats it at the column. No Phase 3 story
may query the six tables; if one needs to, that is a scope change and needs its
own record.

## Compliance

```sql
-- No application code references the deferred tables.
-- Run from the repository root; it must print nothing.
grep -rn -E 'segment_rule|experiment_group|campaign' web/ --include='*.py'
```

The tables' presence in the schema is checked by the existing CI database job,
which runs the three scripts and counts rows.

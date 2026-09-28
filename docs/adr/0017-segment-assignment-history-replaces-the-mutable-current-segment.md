# ADR-0017 — Segment assignment history replaces the mutable current segment

**Status:** Accepted
**Owner:** Marcelo
**Issue:** —
**Supersedes:** —
**Superseded by:** —

---

## Context

[ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md)
makes the Flask monolith one component of the distributed second delivery and
requires that web system to keep working and grow. The analytics layer is the
monolith's part of that delivery. Constraints C-1 and C-2 in
[`scope.md`](../scope.md) remain the first delivery's boundary; they do not
constrain this second-delivery decision.

[`roadmap.md`](../roadmap.md) carries forward the rule that segment assignments
are never updated in place. A run closes the open assignment and inserts its
successor. Migration compares stable segment labels, because identifiers can
change between runs. [ADR-0004](0004-model-ahead-of-the-deferred-segmentation-modules.md)
already calls `customer.current_segment_id` a known-wrong shape and predicts
its replacement by an assignment table with validity dates. This record fulfils
that prediction; it does not supersede the decision that made the first
delivery possible.

F3-10 (#102) now calculates quintile R, F and M scores over a chosen sales
window in `web/db/segments.py`. It selects the lowest matching active
`segment_id`, updates `customer.current_segment_id` only when the value changes,
and clears that column for customers with no sales in the window. The service
commits the statement and logs a transient summary, but the database stores
neither a run nor its raw measures and scores. A later run therefore destroys
the previous assignment except for incidental audit rows.

The existing `segment.valid_from` and `segment.valid_to` describe when a segment
definition applies. They do not say when an individual customer held that
label. What remains uncertain is whether later analytics will need backdated
corrections to closed assignment periods. The second-delivery run writes
forward in execution order, so this record does not add that operation.

## Decision

The monolith retires `customer.current_segment_id` and records every completed
calculation as a `segmentation_run` with its identifier, execution time,
scored window in days, method, parameter snapshot, produced customer count,
and executing user; for each customer in that run it inserts one
`customer_segment_history` row containing the customer, run, nullable stable
segment `label_code`, raw recency, frequency and monetary values, their R, F
and M scores, `valid_from`, and `valid_to`. One service-owned transaction
creates the run, closes each prior open row at the new run's execution time,
and inserts the successor with the same time as `valid_from`, preserving
unassigned results and rolling back the whole run on failure. A unique
customer-and-run pair prevents duplicate results within a run, and a partial
unique index on `customer_id` where `valid_to IS NULL` prevents two open
assignments; this is preferred to a range exclusion constraint because the
required invariant concerns the open row, while exclusion over UUID equality
and timestamp ranges would add GiST operator support and semantics for
backdated closed periods that this delivery does not permit.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep `customer.current_segment_id` as a cache beside history | The current value and the open history row become two writable sources of truth. PostgreSQL cannot enforce their equality with a foreign key or check constraint, so a missed dual write or a direct update can make customer detail disagree with migration reports without violating either schema |
| Store only timestamped customer-history rows, with no run entity | Method, parameters, window, executor, and totals would be repeated once per customer and could disagree within one calculation. A run producing no rows could not be represented, and grouping near-equal timestamps cannot prove that rows belong to the same atomic calculation |
| Soft-delete and reinsert the customer row for every assignment | `customer_id` is the identity referenced by sales, preferences, experiment groups, and an optional user account. Replacing that row either breaks those references or rewrites unrelated relationships merely to retain one changing fact |
| Use `segment.valid_from` and `segment.valid_to` as assignment history | Those dates apply once to a shared segment definition. They cannot express different membership periods for many customers, identify the producing run, or preserve each customer's raw measures and scores |

## Consequences

**What this makes easy.** Every run is traceable through its recorded method,
parameters, measures, scores and outcomes. Segment migration compares labels
between two runs without reconstructing state from the generic audit log. The
open row answers the present-tense question, while closed rows retain the
evidence needed for trends and reports. The partial unique index also rejects
concurrent writers even when application checks are bypassed.

**What this makes hard.** One calculation writes a run row plus one history row
per customer and closes the preceding rows, including when labels do not change.
The work must remain atomic under [ADR-0014](0014-service-owned-transactions-and-typed-write-failures.md),
so a large customer population lengthens one transaction. The partial unique
index does not reject overlap between two already closed intervals. Supporting
backdated correction would require a new decision, interval validation, and
probably a range exclusion constraint.

**What must now be true elsewhere.** Every read of "current segment" queries
the history row whose `valid_to` is null. Today those reads are
`list_customers`, `get_customer`, and `list_customers_in_segment` in
`web/db/customers.py`, with `customer_detail` and `segment_detail` in
`web/routes/catalog.py` consuming them for HU-10 / F3-05 (#65). F3-10 (#102)
must stop updating and clearing the customer column; `web/db/segments.py`,
`web/services/segmentation.py`, `web/routes/segment_run.py`, and
`tests/test_segment_run.py` must create and report the durable run instead.
[`sql/01_schema.sql`](../../sql/01_schema.sql), `sql/02_seed_30_per_table.sql`,
[`data-model.md`](../data-model.md), `business-rules.md`, requirements, stories,
and demonstration evidence must describe the two entities and remove the old
column. The new tables still require the repository's 4NF justification and
seed policy. Existing execution and read permissions remain default-deny under
[ADR-0007](0007-permissions-in-code-with-a-default-deny-middleware.md); this
record neither creates roles nor changes the twice-enforced single-administrator
rule.

## Compliance

```bash
# The retired source of truth has no schema or application reference.
# Must print nothing.
grep -rn 'current_segment_id' sql/01_schema.sql web/ tests/

# Unit and route tests cover the rewritten run and current-segment reads.
pytest tests/test_segment_run.py tests/test_catalog.py

# Both queries must return zero rows after two successful runs.
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 <<'SQL'
SELECT customer_id
FROM customer_segment_history
WHERE valid_to IS NULL
GROUP BY customer_id
HAVING count(*) > 1;
SELECT r.run_id
FROM segmentation_run AS r
LEFT JOIN customer_segment_history AS h ON h.run_id = r.run_id
GROUP BY r.run_id, r.customer_count
HAVING count(h.run_id) <> r.customer_count;
SQL

# Integration tests force failure after closure and expect full rollback; make
# concurrent open rows fail at the partial index; and verify adjacent validity
# times plus current catalog reads from only the open row.
```

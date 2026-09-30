# #352 — Tied measures are split across quintiles by customer id

Evidence that customers who tie on recency, frequency or spend now receive the
same quintile score — the RFM convention the bug report names — instead of being
split across up to five different scores by an arbitrary tiebreak on their id.

Covers the bug's own reproduction steps exactly, plus a mixed (partially tied)
population and downstream code this fix does not touch. New business rule RN-51.
No schema change, and the fix is confined to one SQL statement.

## The defect, confirmed before touching anything

`web/db/segments.py`'s `_SCORE_AND_MATCH` scored each measure with
`ntile(5) OVER (ORDER BY measure DESC, customer_id)`. `ntile` splits *rows* into
five equal-sized groups regardless of which rows share a value; ordering by
`customer_id` after the measure made that split deterministic (the same result on
every re-run) but did nothing to stop it, since it is the row order — including
the tiebreak — that `ntile` cuts on. The module's own docstring claimed the
opposite ("customers who tie … are always cut into the same quintile"), which was
wrong; that sentence is what the fix corrects, alongside the query itself.

Reproduced directly, before any change, against the exact scenario the bug names:
30 rows sharing one value, `ntile(5)` ordered by that value then a tiebreak:

```
n  | bucket
1  |      1
2  |      1
...
6  |      1
7  |      2
...
30 |      5
```

Six rows per bucket, 1 through 5 — an identical value, five different scores.

## The fix

Each measure's five quintile boundaries are now computed once, over the
*distinct* values the window holds, and every customer joins back to their own
value's boundary:

```sql
r_bounds AS (
    SELECT last_purchase, %s + 1 - ntile(%s) OVER (ORDER BY last_purchase DESC) AS r
    FROM (SELECT DISTINCT last_purchase FROM window_sales) AS distinct_r
),
-- f_bounds, m_bounds the same shape, over frequency and monetary
scored AS (
    SELECT w.customer_id, rb.r, fb.f, mb.m
    FROM window_sales AS w
    JOIN r_bounds AS rb ON rb.last_purchase = w.last_purchase
    JOIN f_bounds AS fb ON fb.frequency = w.frequency
    JOIN m_bounds AS mb ON mb.monetary = w.monetary
)
```

A boundary is now a property of a *value*, never of a *row*, so two customers who
share a value always share the boundary they join to. `customer_id` no longer
appears in any quintile's ordering at all — it was never needed for correctness,
only (incorrectly) assumed to provide it.

## How this run was produced

Against the database the three ordered scripts build from empty, exactly as the
bug's own reproduction steps describe, with the application run as the restricted
role `retail_app`.

## The bug's own reproduction, after the fix

Every one of the seed's 30 customers has exactly 10 purchases — the scenario the
report names precisely:

```
distinct purchase counts across the 30 seeded customers: [10]

run 31: RFM_RULES processed=30

frequency_count -> distinct f_scores given (bug if more than one per row):
  frequency=10: f_scores=[5]  (30 customers)
```

The bug's own reproduction query, run against this result
(`SELECT frequency_count, array_agg(DISTINCT f_score) FROM customer_segment_history
WHERE run_id = 31 GROUP BY 1`), now returns exactly one row, with exactly one
score in the array. All 30 tied customers take the best score (5): with a single
shared value and nothing to tell them apart by, the seed's frequency measure
carries no information at all, and the one distinct value falls first in its own
ordering.

**No regression to the normal case.** Recency and monetary, which do vary across
the seed's customers, still split cleanly into five even groups:

```
R score distribution: [(1, 6), (2, 6), (3, 6), (4, 6), (5, 6)]
M score distribution: [(1, 6), (2, 6), (3, 6), (4, 6), (5, 6)]
```

## A mixed population: some customers tied, others not

In a transaction that was rolled back, 12 of the 30 customers were given one more
purchase each (frequency 10 → 11), so the run scores a population split between
two tied groups rather than one:

```
run 32: processed=30

mixed population -- frequency_count -> f_scores:
  frequency=10: f_scores=[4]  (18 customers)
  frequency=11: f_scores=[5]  (12 customers)

independent SQL cross-check -- pairs with equal frequency but different f_score: 0
```

The cross-check is a second, independently written query
(`SELECT … FROM customer_segment_history a JOIN customer_segment_history b ON
a.frequency_count = b.frequency_count AND a.customer_id < b.customer_id WHERE
a.f_score <> b.f_score`), over every pair of customers in the run, not only the
grouped view above: zero pairs disagree.

## What this does not touch

`explain_migration` (#338) already reads whatever `r_score`/`f_score`/`m_score`
were stored for a run and explains, in words, when a score moved because "other
customers moved the quintile cut points" rather than the customer's own
behaviour (RN-50). It does not compute or duplicate the quintile mechanism
itself, so this fix changes nothing about it — if anything, its explanations are
now more trustworthy, since a score can no longer move for the wrong reason
(a different customer id breaking a tie differently) alongside the reasons it
already describes.

## Tests

`tests/test_segments_pipeline_db.py`'s
`test_every_quintile_is_ordered_deterministically` asserted the old, incorrect
property (every quintile's ordering names `customer_id`) and is rewritten as
`test_a_quintile_boundary_is_computed_once_per_distinct_value`, asserting the
opposite — no quintile's ordering names `customer_id` at all — and that each
measure's distinct-value CTE is present in the statement.

```
$ pytest -q
2779 passed         # same total as develop: one test rewritten, none added or removed
$ black --check .   # All done
$ ruff check .      # All checks passed!
```

## A numbering collision found, not caused, while placing this fix's own rule

Before adding RN-51, every existing rule number was checked, and two pre-existing
collisions turned up: **RN-47** (`docs/business-rules.md:215` and `:365`) and
**RN-50** (`:388` and `:868`, both used by different, unrelated rules). Neither
involves this fix or its own rule, and untangling either means tracing every
cross-reference each of the four already has across other stories' code and
evidence — out of scope for #352. Flagged here for the team to fix in its own
change, the same way this document flags rather than silently fixes anything
outside its own bug.

## Things to know

* **Only the worst-scored-first ordering changes; every downstream reader is
  untouched.** `ScoredCustomer`'s shape, the segment-matching join, `RN-46`'s
  fallback, and every consumer of `r_score`/`f_score`/`m_score` read exactly the
  same columns as before.
* **With fewer distinct values than five, a run's worst scores go unused** for
  that measure, rather than being manufactured to fill all five — the same
  honest "some buckets can be empty" principle this project already applies to
  K-means's own quintile-shaped heatmap (RN-37) and label mapping (RN-38).

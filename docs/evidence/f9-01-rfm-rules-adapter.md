# F9-01 — RFM_RULES as an adapter behind a method-agnostic pipeline

Evidence that the existing rule-based scoring now runs as one named method of a
shared pipeline: it emits one labelled assignment per customer, the pipeline
records the run and those assignments without knowing which method produced them,
and everything downstream of that boundary reads label codes and never the method
(ADR-0018).

Covers the four acceptance criteria on F9-01 and the three checks ADR-0018's
Compliance section names. No schema change.

## What changed, and why it had to

F3-10 scored, matched and wrote in **one** SQL statement. That made a run atomic
and repeatable, but it also meant nothing else could reuse the writing: a second
method would have had to copy the closing of old rows, the run row and the
inserts. The statement is now two halves:

| Half | Where | Knows about the method? |
|---|---|---|
| **Decides**: quintile scores matched to the segment bands | `score_rfm_rules` in `web/db/segments.py`, wrapped by the `MethodAdapter("RFM_RULES", rfm_rules_adapter)` registration in `web/services/segmentation.py` | The frozen adapter declares its method |
| **Records**: creates the run, closes the open rows, opens one row per customer | `create_run`, `close_open_assignments`, `insert_assignments`, driven by `run_method` | No |

A supplied adapter is accepted only when its declared method equals the requested
method. `run_method` raises `AdapterMismatch` before calling the adapter or reading
or writing the database, preventing one strategy's output from being recorded
under another method's name.

`run(connection, window_days)` keeps its signature, so the segment-run page and its
tests are untouched. The old `_RECALCULATE` statement and `recalculate_segments`
are deleted rather than left beside the pipeline: two ways to run one method,
only one of them tested, is the failure this refactor exists to prevent.

## How this run was produced

Against the database the three ordered scripts build from empty, with the old code
taken from the base commit and the new code from this branch, so the two could be
compared on the same starting state.

```
customers 30 · transactions 300 · segmentation runs 30
```

## The new pipeline writes exactly what the old statement wrote

For each window, from the same starting state, the old statement was run and its
result captured, rolled back; then the new pipeline, captured, rolled back. The
comparison covers every history row (label, segment, raw recency/frequency/
monetary, the three scores), the set of open assignments, the counts the run page
reports, and the audit entries the triggers wrote.

```
window | old counts (proc,assigned,unmatched,reassigned,cleared) | same rows | same open set | same counts
   180 |                    (30, 30, 0, 29, 0)                    |   True    |     True      |    True      audit rows old=60 new=60
    90 |                    (30, 30, 0, 29, 0)                    |   True    |     True      |    True      audit rows old=60 new=60
    30 |                    (30, 30, 0, 29, 0)                    |   True    |     True      |    True      audit rows old=60 new=60
     7 |                    (30, 7, 23, 7, 23)                    |   True    |     True      |    True      audit rows old=60 new=60
     1 |                    (30, 1, 29, 1, 29)                    |   True    |     True      |    True      audit rows old=60 new=60
```

The windows of 7 and 1 day leave 23 and 29 customers with no sales, so they also
exercise the unassigned result (RN-21) at scale. One difference is deliberate: the
run's `parameters` were `{'window_days': 1}` and are now
`{'quintiles': 5, 'window_days': 1}`, because the number of quintiles is a parameter
of the method and a run should record everything that shaped it.

## The acceptance criteria, one by one

**A completed RFM_RULES run records its method, window and parameters.** Two real
committed runs through the pipeline; the second, over the same sales, changed
nothing:

```
run 41: RFM_RULES processed=30 assigned=30 reassigned=29
run 42: RFM_RULES processed=30 assigned=30 reassigned=0  <- same sales, nothing changed
recorded on the run: ('RFM_RULES', 180, {'quintiles': 5, 'window_days': 180}, 30)
```

**Every assigned customer carries a label from the vocabulary and its raw R/F/M
values and scores.** Both ADR-0018 compliance queries return zero rows after those
runs:

```
labels outside the vocabulary         : 0
scored customers left unlabelled      : 0
```

Before any row is written the pipeline also refuses a customer who was measured but
left unlabelled, a labelled customer missing any raw recency, frequency or monetary
value, and the same customer twice in one run. Quintile scores remain optional
because methods such as K-means do not produce them
(`tests/test_segmentation_pipeline.py`).

**A value outside `RFM_RULES` or `KMEANS` is refused by the database.** The service
refuses it first, before it reads a row; the column's `CHECK` is the guard a second
writer cannot skip, and a test pins that the two list exactly the same domain:

```
method 'RFM_RULES'  -> accepted
method 'KMEANS'     -> accepted
method 'HDBSCAN'    -> REFUSED: new row for relation "segmentation_run" violates check constraint "segmentation_run_method_check"
method 'kmeans'     -> REFUSED: new row for relation "segmentation_run" violates check constraint "segmentation_run_method_check"
method ''           -> REFUSED: new row for relation "segmentation_run" violates check constraint "segmentation_run_method_check"
```

**A consumer reading a run's assignments is not given the method.** Four things
hold, each checked:

* an `Assignment` has no field naming a method or a cluster;
* no consumer (`classify_migration`, `build_migration_matrix`, `explain_migration`)
  takes a parameter naming one;
* none of the three readers of a run's assignments selects the method from the run
  (`list_run_labels`, `list_run_assignments`, `get_customer_assignment_for_run`);
* the pipeline writes the same rows whichever method it is told it is running.

On the real database, a run written as `RFM_RULES` and one written as `KMEANS` were
compared by the unchanged migration code, which was passed run ids and the label
vocabulary and never a method:

```
latest runs: [(49, 'KMEANS'), (42, 'RFM_RULES')]
compute_migration(run 42 RFM_RULES -> run 49 KMEANS): 30 customers, 22 moved
what a consumer receives per assignment: ['customer_id', 'customer_name', 'segment_id',
  'label_code', 'r_score', 'f_score', 'm_score', 'recency_last_purchase_at',
  'frequency_count', 'monetary_total']
```

The `KMEANS` run there was written by a **stand-in adapter that ranks customers by
spend and cuts the ranking into five labels**. It is not K-means and says so in its
parameters (`stand_in: true`); it exists only to give the pipeline a second method
to be indifferent to, before the real adapter exists (F9-02, F9-03).

## Atomicity, and the two ADR-0017 checks

A run is one transaction. An adapter was made to produce an assignment with a label
that is not in the vocabulary, which the database refuses only at the *last* write,
after the open rows were already closed:

```
insert refused by the database: insert or update on table "customer_segment_history" violates foreign key constraint ...
history rows 960 -> 960, open rows 30 -> 30, runs 32 -> 32
```

Nothing changed: the run row, the closures and the inserts rolled back together.
After the two committed runs above, ADR-0017's queries both return no rows, and every
row of a run was closed at the instant the next run opened:

```
customers with two open rows          : 0
runs whose rows != customer_count     : 0
run 41 rows closed at the instant the next run opened: 30 of 30
```

## Tests

`tests/test_segmentation_pipeline.py` (39 tests) and
`tests/test_segments_pipeline_db.py` (17) are new. ADR-0018's selection runs:

```
$ pytest -q tests/test_segmentation_pipeline.py -k 'method_domain or cluster_id_permutation or downstream_method_independence'
15 passed, 24 deselected
```

`cluster_id_permutation` selects nothing yet, on purpose: it belongs to F9-03, which
owns the mapping from clusters to labels, and there is nothing to permute before it.

Existing tests changed, because they inspected or fed the statement that no longer
exists:

| Test | What happened to it |
|---|---|
| `test_the_window_is_a_parameter_and_never_interpolated`, `test_every_quintile_is_ordered_deterministically` | Moved to `test_segments_pipeline_db.py`, now against `score_rfm_rules` |
| `test_the_statement_tracks_assignment_changes_for_result_counts`, `test_the_counts_come_back_in_the_order_the_statement_selects_them` | Removed: they asserted on a CTE and a 5-column row that are gone. What they protected, the counts, is `summarise`'s tests and the comparison above |
| `test_the_run_commits_so_the_audit_entries_survive`, `test_segment_completion_is_logged_only_after_commit` | Rewritten with doubles for the new calls, keeping their intent: one commit after every write, and the success event only after it |

```
$ pytest -q
1075 passed
$ black --check .   # All done
$ ruff check .      # All checks passed!
```

## Things to know

* **`cleared` counts a first-ever unassigned customer.** A customer with no open row
  counts as changed, so if they have no label they are counted as cleared. The old
  statement did exactly this; it is kept so a refactor cannot move the numbers the
  run page reports, and pinned by a test that names it.
* **`KMEANS` is in the domain and has no adapter yet.** `run_method` raises
  `MethodUnavailable` for it. The adapter arrives with F9-02 and carries F9-03's
  mapping; supplying it to `run_method`, and not registering it, means no run can
  reach the database before the mapping exists.
* **`docs/demo-system-guide.md` reflects the durable pipeline.** It describes the
  open history row as current, the worst-ranked-label fallback for a scored
  customer with no matching band, and the successor row written for every customer
  on every run.

## Not evidenced here

The K-means fit (F9-02), the mapping from clusters to labels (F9-03), and the
comparison view (F9-04).

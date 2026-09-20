# ADR-0018 — Two segmentation strategies feed one label-based, method-agnostic pipeline

**Status:** Proposed
**Owner:** Marcelo
**Issue:** —
**Supersedes:** —
**Superseded by:** —

---

## Context

[ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md)
reinstates the distributed architecture for the second delivery. This record is
the monolith's analytics phase within that same delivery. The microservices,
Android client, desktop client, MongoDB, Redis and shared JWT remain part of the
delivery. C-1 and C-2 in [`scope.md`](../scope.md) describe the first delivery;
they do not prohibit the second delivery's integration contracts.

The monolith already has a narrow RFM recalculation in
`web/services/segmentation.py`. [`sql/01_schema.sql`](../../sql/01_schema.sql)
holds `segment_rule`, `segment` and the mutable `customer.current_segment_id`,
but no stable label code, run, assignment history or K-means strategy. That
shape was intentional for the first delivery.
[ADR-0004](0004-model-ahead-of-the-deferred-segmentation-modules.md)
predicted that the mutable column would be replaced when segment history
arrived; it is not contradicted or superseded here.

[ADR-0017](0017-segment-assignment-history-replaces-the-mutable-current-segment.md)
fulfils that prediction with `segmentation_run` and `customer_segment_history`.
This record decides what may produce a run and what its consumers may learn.

K-means cluster ids cannot cross that boundary. They are arbitrary names
assigned by one fit, not business identities. With the same customers and the
same partition, one run may call a cluster `3` and the next may call it `1`.
A report comparing those ids can claim 100% migration when no customer moved.
The result looks plausible, raises no error and reaches a dashboard as false
business information.

[`roadmap.md`](../roadmap.md) already warns that migration is a change of stable
segment label, not segment id. This record promotes that carried-forward warning
into a second-delivery decision and makes it apply equally to rule-based and
K-means runs. The judgement is how to label an unordered result without
teaching every consumer clustering.

## Decision

`segmentation_run.method` is exactly `RFM_RULES` or `KMEANS`, and each
strategy produces one run plus one assignment carrying a stable segment label
code for every customer; after that boundary, assignment history, migration
detection, migration matrices, dashboards and recommendations read the label
code and never branch on the method or consume a raw cluster id. `RFM_RULES`
emits the label code selected by the matching RFM band. `KMEANS` min-max
normalizes each feature across the run to `[0,1]`, reverses recency so higher
always means better, and maps a constant feature to `0`; it orders centroids
by descending `R + F + M`, breaks score ties by descending `R`, then `F`, then
`M`, and breaks an identical-centroid tie by the lexicographically smallest
customer id in the cluster; it then pairs that order with the configured
stable label codes in their declared best-to-worst business order, whose size
must equal `k`. This rule is chosen because it is deterministic from the
current run's data, preserves the existing higher-is-better RFM orientation,
and does not depend on an earlier run's ids or centroids.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep only RFM rules and omit K-means | The second-delivery analytics plan includes batch clustering and its quality metrics. Omitting it leaves no way to compare a rule-defined segmentation with a data-derived partition and turns the existing narrow first-delivery recalculation into an accidental permanent boundary |
| Let every consumer branch on `segmentation_run.method` | Migration, dashboards and recommendations would each need two assignment readers and two sets of tests. Adding a third method would require changing every consumer, and one missed branch could compare cluster ids while another compares labels, producing different migration totals from the same runs |
| Persist raw cluster ids beside label codes and let reports choose | A raw id has meaning only inside one K-means fit. Exposing it to the reporting schema preserves the exact invalid join this record prevents: two plausible integers compare cleanly, report 100% migration and raise no database or application error |
| Match each new cluster to the previous run's nearest centroid | The mapping becomes path-dependent: replaying a run after a different predecessor can change its labels. The first run has no predecessor, a changed `k` has no one-to-one match, and cluster splits or merges force an arbitrary choice precisely when migration reporting must remain explainable |

## Consequences

**What this makes easy.** A downstream feature has one assignment contract.
Migration is a comparison of label codes, regardless of whether either run used
rules or K-means. Dashboards and recommendations can compare runs produced by
different methods without carrying clustering vocabulary. A test can permute
raw K-means cluster ids and prove that the labelled assignments do not change.

**What this makes hard.** K-means needs an explicit ordered label vocabulary,
feature normalization and validation that the label count equals `k`. The
commercial meaning of a business label is reduced to the declared order; two
centroids with similar totals can exchange labels when their R/F/M ordering
crosses, even if the customer movement is small. The final customer-id tie-break
is deterministic but has no commercial meaning. Raw cluster ids may still be
useful for model diagnostics, but they cannot enter assignment history or any
reporting contract. A customer with no sales in the scored window has no R/F/M
to cluster, so the run records the unassigned result ADR-0017 preserves rather
than forcing a label; migration reporting has to treat that null as a state, not
as a missing value.

**What must now be true elsewhere.** ADR-0017's `segmentation_run` and
`customer_segment_history` implementation must carry the method at the run and
the stable label code at the assignment boundary. The Stage 3 stories for the
RFM adapter, K-means adapter, migration report, migration matrix, dashboards
and recommendations inherit this contract. [`data-model.md`](../data-model.md)
and [`sql/01_schema.sql`](../../sql/01_schema.sql) must describe and enforce the
method domain, the ordered label vocabulary and the history relationship before
those stories merge. The service boundary continues to follow
[ADR-0003](0003-layered-architecture-with-an-explicit-service-layer.md), and a
run plus all of its assignments remains one service-owned transaction under
[ADR-0014](0014-service-owned-transactions-and-typed-write-failures.md).

## Compliance

The implementation supplies contract tests with these exact cases. They are
executable with the repository test suite:

```bash
pytest -q tests/test_segmentation_pipeline.py -k \
  'method_domain or cluster_id_permutation or downstream_method_independence'

# method_domain refuses other values and a KMEANS run whose k differs from the
# configured label count. cluster_id_permutation renames every cluster id in a
# fixed partition and expects identical (customer_id, label_code) rows.
# downstream_method_independence gives equal labelled assignments from each
# method to every consumer; inputs exclude method/raw_cluster_id, outputs match.
```

Both database checks must return zero rows after any run:

```sql
-- A label, where one was assigned, always comes from the declared vocabulary.
-- A raw cluster id can never reach the assignment history.
SELECT h.customer_id, h.run_id, h.label_code
FROM customer_segment_history AS h
WHERE h.label_code IS NOT NULL
  AND h.label_code NOT IN (SELECT label_code FROM segment_label);

-- A customer the run actually scored is always labelled. A null label is
-- valid only for the unassigned result ADR-0017 preserves: no sales in the
-- scored window, so no R/F/M to cluster or match.
SELECT h.customer_id, h.run_id
FROM customer_segment_history AS h
WHERE h.label_code IS NULL
  AND h.r_score IS NOT NULL;
```

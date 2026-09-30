# ADR-0030 — K-means clusters are paired with the stable labels by proportional rank, so k need not equal the vocabulary's size

**Status:** Proposed
**Owner:** Raquel
**Issue:** #336
**Supersedes:** [ADR-0018](0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md)
**Superseded by:** —

---

## Context

[ADR-0018](0018-two-segmentation-strategies-behind-one-method-agnostic-pipeline.md)
decided that both segmentation strategies feed one label-based pipeline: a run
writes a stable label code for every customer, and nothing downstream branches on
the method or sees a raw cluster id. For K-means it fixed a deterministic order —
centroids by descending normalised `R + F + M`, then `R`, then `F`, then `M`, then
the lexicographically smallest customer id — and paired that order with the label
vocabulary one to one, "whose size must equal `k`".

That last clause makes `k` a constant. The vocabulary in `segment_label` has six
labels, so every K-means run is `k = 6`, and any other value is refused before a
sale is read (`VocabularySizeMismatch`). The instructor's retrospective of the
first delivery asked to compare models with a different `k` (its example is
`K: 5`), and the model comparison page (F9-04) cannot compare a model that cannot
be run. Issue #336 asks for any `k`, mapped to stable labels by a documented rule,
with the run recording when several clusters share a label.

Everything in ADR-0018 except the equality clause still holds and is restated
here unchanged, so this record can replace it whole, as the ADR process requires.

The judgement calls are the pairing rule itself, what a half position rounds to,
and the smallest `k`. None of them has a data-derived answer: they are ways of
reading an ordered list of clusters as an ordered list of labels.

## Decision

`segmentation_run.method` is exactly `RFM_RULES` or `KMEANS`, and each strategy
produces one run plus one assignment carrying a stable segment label code for
every customer; after that boundary, assignment history, migration detection,
migration matrices, dashboards and recommendations read the label code and never
branch on the method or consume a raw cluster id. `RFM_RULES` emits the label code
selected by the matching RFM band. `KMEANS` min-max normalizes each feature across
the run to `[0,1]`, reverses recency so higher always means better, and maps a
constant feature to `0`; it orders centroids by descending `R + F + M`, breaks
score ties by descending `R`, then `F`, then `M`, and breaks an identical-centroid
tie by the lexicographically smallest customer id in the cluster; it then pairs
that order with the configured stable label codes in their declared best-to-worst
business order **by proportional rank**: of `k` clusters and `V` labels, the
cluster in position `i` (0 = best) takes the label in position
`i × (V − 1) / (k − 1)`, rounded to the nearest position and, **when it falls
exactly halfway, towards the worse label**, computed in integers. The best cluster
therefore always takes the best label and the worst cluster the worst label; when
`k = V` every cluster takes the label in its own position, which is ADR-0018's
one-to-one pairing unchanged; with `k < V` some labels take no cluster, and with
`k > V` some take several. `k` is at least 2. The run records the pairing in
`segmentation_run.parameters.label_mapping` — the label of each position, the
clusters per label, the labels shared and the labels unused — keyed by label code
and position only, never by cluster number.

For the six-label vocabulary (CHAMPION, LOYAL, POTENTIAL, AT_RISK, HIBERNATING,
LOST), best cluster first:

| k | Labels |
|---|---|
| 2 | CHAMPION, LOST |
| 4 | CHAMPION, POTENTIAL, AT_RISK, LOST |
| 5 | CHAMPION, LOYAL, AT_RISK, HIBERNATING, LOST — POTENTIAL unused |
| 6 | CHAMPION, LOYAL, POTENTIAL, AT_RISK, HIBERNATING, LOST — as under ADR-0018 |
| 8 | CHAMPION, LOYAL, LOYAL, POTENTIAL, AT_RISK, HIBERNATING, HIBERNATING, LOST — LOYAL and HIBERNATING shared |

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep ADR-0018's equality and refuse any other `k` | It is what #336 reports: `k` is fixed at six, and the model comparison the retrospective asked for cannot be made |
| Pair each cluster with the label whose reference centroid is nearest | No label has a centroid. The nearest candidates are the RFM rule bands, but those are quintile scores — a rank among customers — while K-means works on min-max-normalised raw values, so the distance would compare two scales. Taking references from an earlier run's centroids makes a run's labels depend on its predecessor, which ADR-0018 rejected. And `k = 6` would no longer be guaranteed to label as before |
| Pair the first `min(k, V)` positions one to one and give every remaining cluster the worst label | Order-preserving, but it piles every extra cluster into LOST, so a customer's label would depend on how many clusters were asked for below them rather than where they stand |
| Cut the normalised `R + F + M` sum into `V` fixed bands | Absolute rather than relative, so a run where everyone improved would label everyone better; it also changes `k = 6` labelling, since six clusters need not fall one per band |
| Round a half position towards the better label | Equally deterministic. Towards the worse is chosen so that an ambiguous cluster is never presented as better than its position supports, the cautious reading for a commercial label |
| Allow `k = 1` | A single cluster has no order: every customer would take whichever label the rule pins position 0 to, which says nothing about them |

## Consequences

**What this makes easy.** Any `k` from 2 up can be run and compared, including on
the model comparison page, without any consumer learning about clusters. `k = 6`
labels exactly as before, so existing runs, tests and evidence keep their meaning.
The pairing needs nothing but the declared order of the vocabulary, depends only on
the current run, and is computed in integers, so it is reproducible from what the
run records.

**What this makes hard.** With `k ≠ V` **a label is a relative position, not a
fixed commercial meaning.** Under `k = 5`, AT_RISK is "the middle of five groups";
under `k = 8` it is "the fifth of eight". The words are the same and the
definitions are not. As a result, **comparing runs with different `k` — in the
migration matrix, the migration explanation, the segment history report or a
customer's timeline — can show migrations caused by the change of model, not by any
change in the customer's behaviour.** A customer who bought exactly the same can
move from POTENTIAL to AT_RISK simply because a `k = 5` run has no POTENTIAL. This
record does not solve that; it is left to a separate issue, and until then those
pages compare labels as they always have. A `k < V` run leaves some labels with no
customer, which the dashboard shows as zero (RN-41) and the run records; a
`k > V` run merges several clusters under one label, so the label counts no longer
show the fit's full granularity — the run's cluster sizes still do. ADR-0018's
costs still apply: a label's meaning is reduced to the declared order, two
centroids with similar totals can exchange positions, and the customer-id
tie-break has no commercial meaning.

**What must now be true elsewhere.** RN-38 in
[`business-rules.md`](../business-rules.md) states the pairing, the rounding and
the minimum. `web/services/cluster_labels.py` is still the only place a cluster
becomes a label. Everything ADR-0018 required of ADR-0017's tables, of
[ADR-0003](0003-layered-architecture-with-an-explicit-service-layer.md)'s service
boundary and of [ADR-0014](0014-service-owned-transactions-and-typed-write-failures.md)'s
transactions still holds. `label_mapping` in a run's parameters is diagnostic: no
consumer may key on it to recover a cluster, and it holds no cluster number to
recover.

## Compliance

The implementation supplies contract tests with these exact cases. They are
executable with the repository test suite:

```bash
pytest -q tests/test_segmentation_pipeline.py -k \
  'method_domain or paired_by_rank or cluster_id_permutation or downstream_method_independence'

# method_domain refuses other values and a KMEANS run of a single cluster.
# paired_by_rank runs a k other than the label count and writes only vocabulary
# label codes. cluster_id_permutation renames every cluster id in a fixed
# partition and expects identical (customer_id, label_code) rows.
# downstream_method_independence gives equal labelled assignments from each
# method to every consumer; inputs exclude method/raw_cluster_id, outputs match.

pytest -q tests/test_cluster_labels.py -k 'pairing_table or half_rounds or best_is_always'
# the table above, the half towards the worse label, and for every k from 2 to 14
# over vocabularies of 1 to 8 labels: best to best, worst to worst, order kept.
```

ADR-0018's two database checks still apply and must return zero rows after any
run, whatever its `k`:

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

And a K-means run records how it paired, keyed only by label code:

```sql
-- Every K-means run since this record lists its pairing, and no key of
-- clusters_per_label is anything but a label code.
SELECT r.run_id
FROM segmentation_run AS r
CROSS JOIN LATERAL jsonb_object_keys(r.parameters -> 'label_mapping' -> 'clusters_per_label') AS key
WHERE r.method = 'KMEANS'
  AND key NOT IN (SELECT label_code FROM segment_label);
```

# F9-03 — K-means clusters mapped to stable labels

Evidence that a K-means cluster becomes a business label by what it *contains* and
never by what it is *called*, so that a migration report cannot mistake a renumbered
cluster for a customer who moved (ADR-0018). With this story a K-means run can be
started for real.

Covers the five acceptance criteria on F9-03 and business rule RN-38. It builds on
F9-02's fit. No schema change, no new dependency, no new permission, and no page.

## What this delivers

| | Where |
|---|---|
| The mapping: order clusters by their centroid's R + F + M, break ties, pair with the vocabulary | `web/services/cluster_labels.py` |
| The run: read the vocabulary, refuse a k that is not its size, fit, map, hand the labelled assignments to the pipeline | `run_kmeans` in `web/services/segmentation.py` |

`run_kmeans(connection, window_days, params)` is how a `KMEANS` run is started.
`run_method` still raises `MethodUnavailable` for it, because it has no default k or
seed to give. The adapter `run_kmeans` builds is a `MethodAdapter` bound to `KMEANS`,
so `run_method` also refuses it for any other method (`AdapterMismatch`). Everything that F9-02's evidence did with a stand-in mapping is done
here with the real one.

## How this run was produced

Against the database the three ordered scripts build from empty, with **no stand-in
anywhere**: K-means over the real seeded sales, labelled by the real vocabulary
(`segment_label`, best to worst by `ordinal_position`):

```
CHAMPION, LOYAL, POTENTIAL, AT_RISK, HIBERNATING, LOST   -> size 6, so k = 6
```

## The acceptance criteria, one by one

**Centroids are ordered by descending R + F + M, with the ADR's tie-breaks.** Checked
from the *stored* rows, without calling the mapping: the run's raw recency, frequency
and monetary values were read back from `customer_segment_history`, normalised
independently in numpy, and each label's customers averaged.

```
run 31: method=KMEANS processed=30 assigned=30
label        customers   mean R   mean F   mean M    R+F+M
CHAMPION             5   0.6207   0.0000   0.7239   1.3446
LOYAL                6   0.7471   0.0000   0.3786   1.1257
POTENTIAL            4   0.9483   0.0000   0.1135   1.0618
AT_RISK              2   0.0862   0.0000   0.8981   0.9843
HIBERNATING          7   0.1626   0.0000   0.5058   0.6684
LOST                 6   0.3851   0.0000   0.1681   0.5531
R+F+M strictly descending down the vocabulary: True
```

Mean F is 0 for every label because the seed gives every customer exactly 10
purchases, so frequency is a constant feature (F9-02). The tie-breaks, which real data
rarely reaches, are pinned by tests: each of R, F and M decides a tie the earlier keys
leave, and the smallest customer id decides between identical centroids.

**That order is paired with the vocabulary best to worst, and its size equals k.** The
best cluster took `CHAMPION` and the worst `LOST` above. The vocabulary is read by
`ordinal_position` and not by any key order: a test hands the reader a dictionary in
scrambled order and requires the same labels.

**A run whose k differs from the vocabulary size is refused before any assignment is
written.** Stronger than the criterion asks: it is refused before a single sale is
*read*. The sales reads were counted during three refusals:

```
k=5: refused: K-means was asked for k=5 clusters but the label vocabulary has 6 labels. They must be equal, ...
k=7: refused: K-means was asked for k=7 clusters but the label vocabulary has 6 labels. They must be equal, ...
k=2: refused: K-means was asked for k=2 clusters but the label vocabulary has 6 labels. They must be equal, ...
sales read during the refusals: 0 | runs and history rows before/after: (31, 930) (31, 930)
```

**Permuting the raw cluster ids of a fixed partition changes no (customer, label)
pair.** The real 30-customer partition, with its clusters renamed by 500 random
permutations of the 720 possible:

```
500 of 500 random renamings (of 6! = 720 possible) give identical (customer, label) pairs
```

The same is asserted for *every* permutation of a small fit, at the level of the
mapping and at the level of the rows the pipeline writes.

**No raw cluster id appears in `customer_segment_history`.** It has nowhere to go:

```
columns: ['history_id', 'customer_id', 'run_id', 'segment_id', 'label_code',
  'recency_last_purchase_at', 'frequency_count', 'monetary_total', 'r_score',
  'f_score', 'm_score', 'valid_from', 'valid_to']
any column naming a cluster: False
ADR-0018: labels outside the vocabulary : 0
ADR-0018: scored customers unlabelled    : 0
run parameters mention a cluster id / centroid: False
```

## The failure this exists to prevent, on real data

Two different seeds can find the *same* partition and number its clusters differently.
On the seeded customers, seeds 32 and 46 do exactly that:

```
seeds 32 and 46 find the SAME partition of the 30 customers, but number the clusters differently
a report comparing raw cluster numbers would say 23 of 30 customers (76%) moved
compute_migration(run 32 -> run 33), by label: {'UNCHANGED': 30}
```

A report that keyed on cluster numbers would have announced that three quarters of the
customer base migrated, raised no error, and reached a dashboard as business
information. Compared by label, by the migration code F7 wrote and this story did not
touch, nobody moved. That is the whole contract.

The same code also compares a rules run with a K-means run without being told either
method: `run 34 RFM_RULES -> run 32 KMEANS: 30 customers, 22 moved, no method passed`.

## The tests catch a broken mapping

Faults were seeded into the code one at a time, and each was required to make a test
fail. All 11 did:

| Fault seeded | Tests that failed |
|---|---|
| Order by *ascending* R + F + M | 4 |
| A plain sum instead of the exact one | 1 |
| Tie broken by the *lower* R / F / M | 2 / 1 / 1 |
| Identical centroids broken by the *largest* customer id | 2 |
| Tie finally broken by the cluster *number* | 2 |
| Vocabulary paired worst first | 3 |
| Vocabulary size never checked in the mapping | 5 |
| Vocabulary read in dictionary order, not by ordinal | 1 |
| k checked only *after* the sales are read | 6 |

The exact-sum case is worth naming: `0.1 + 0.2 + 0.3` is `0.6000000000000001` and
`0.3 + 0.2 + 0.1` is `0.6`, so with a plain sum two centroids made of the same three
numbers are ranked by rounding instead of tying on the sum and moving on to R. A test
builds exactly that pair.

## ADR-0018's compliance command

```
$ pytest -q tests/test_segmentation_pipeline.py -k 'method_domain or cluster_id_permutation or downstream_method_independence'
17 passed, 24 deselected
```

All three groups are now non-empty (`method_domain` 8, `cluster_id_permutation` 1,
`downstream_method_independence` 8). `cluster_id_permutation` selected nothing after
F9-01, on purpose, because there was nothing to permute before this story.

## Tests

`tests/test_cluster_labels.py` (18) and `tests/test_kmeans_run.py` (15) are new, and
two cases were added to `tests/test_segmentation_pipeline.py`.

```
$ pytest -q
1234 passed         # 1199 on develop, plus 35
$ black --check .   # All done
$ ruff check .      # All checks passed!
```

## Things to know

* **Runs that find the same partition label it alike, but not every seed finds the
  same partition.** K-means falls into worse local optima for some seeds: on a small
  test fixture, seeds 6, 9 and 12 end at an inertia of 0.275 against 0.009 for the
  other nine. That is the algorithm, not the mapping. The guarantee, tested, is that
  *equal partitions get equal labels*. Which of two partitions is the better model is
  what the run's recorded inertia and silhouette are for.
* **The customer-id tie-break has no commercial meaning,** as the issue says. It is
  stated in the code, in RN-38 and here, and it decides only between clusters that are
  the same in every measure.
* **The labels are the declared order and nothing more.** On the seed, the `AT_RISK`
  cluster has the lowest recency and the highest spend, and ranks above `HIBERNATING`
  and `LOST` because R + F + M puts it there. That is ADR-0018's stated limit: a label
  means its position in the declared order.
* **Open for the team: K-means assignments still carry no `segment_id`.** The issue
  does not mention it, so it is left empty. Pages that list a segment's members read
  `segment_id`, so a K-means run's customers do not appear there. Making them appear
  needs a decision on how a label resolves to a segment, since a label can be carried
  by several segments. The migration code and ADR-0018's queries read labels and are
  unaffected.

## Not evidenced here

A page or form to start a K-means run, and the view that compares the two methods'
runs (F9-04).

# F9-02 — K-means, implemented in the application

Evidence that the second segmentation method exists and can be trusted: the K-means
fit is written in the application without a scientific-computing dependency
(ADR-0021), it is reproducible from what the run records, and each of its three
numerical hazards (an empty cluster, a fit that does not converge, a tie between
equally distant centroids) has a defined behaviour that is recorded and tested.
The stored partition is assigned once more after the last centroid update, so its
labels and quality measures use the nearest final centroids rather than the centres
from the preceding iteration.

Covers the acceptance criteria on F9-02 and business rule RN-37. It builds on F9-01's
pipeline. No schema change, no new dependency, no new permission, and no page: how a
person starts a K-means run is not part of this story.

## What this delivers, and what it deliberately does not

| Delivered here | Where |
|---|---|
| The fit: min-max normalisation, k-means++ start, Lloyd's algorithm, empty-cluster refill, convergence and tie rules, quality measures | `web/services/kmeans.py` |
| The `KMEANS` adapter: reads the window's raw R/F/M, clusters the customers who have sales, leaves the rest unassigned, records parameters and quality on the run | `kmeans_adapter` in `web/services/segmentation.py` |

**The mapping from clusters to labels is F9-03's.** ADR-0018 defines it and ADR-0021
repeats that "nothing here lets a raw cluster id into `customer_segment_history`". So
the adapter is *handed* a mapping and never writes a label of its own, and
`run_method` still raises `MethodUnavailable` for `KMEANS` unless one is supplied. No
K-means run can reach the database before F9-03 exists. Everywhere this document
shows a K-means run being written, the mapping is a **stand-in defined in the
verification script** (rank clusters by the centroid's R + F + M and hand out the
labels in order), and is labelled as one. It is not the ADR's rule and is not in the
repository.

## How this run was produced

The database was built from empty by the three ordered scripts (30 customers,
300 transactions, 30 runs). The fit was then checked three independent ways, none of
them the code under test.

scikit-learn and numpy were installed in the verification sandbox **only to check
this code**. ADR-0021 forbids taking them as a dependency, not using them as a
yardstick, and neither appears in any file of this change.

## The fit against independent references

**1. Lloyd's algorithm against scikit-learn, from the same starting centroids.** 300
random datasets (uniform, clustered and skewed; 12 to 300 customers; k from 2 to 7),
each started from *this code's* k-means++ centroids so that only the algorithm is
compared, not the seed:

```
identical partitions          : 300 of 300
largest centroid difference   : 7.77e-16
largest inertia difference    : 5.68e-14
largest silhouette difference : 1.55e-15  (vs sklearn.metrics.silhouette_score)
iterations to converge        : min 2, median 5, max 35
```

**2. On the real seeded features, exact rational arithmetic is the referee.** The 30
customers with sales in the 180-day window, k = 6 (the size of the label vocabulary):

```
customers placed differently from exact rational arithmetic: 0 of 30
scikit-learn, same initial centroids  : places 1 customer(s) differently [6]
   inertia mine 0.541445 | sklearn 0.535394
```

scikit-learn disagrees on one customer here, and the disagreement was investigated
and not waved through. Customer 6 sits at squared distances from clusters 5 and 0 that
differ by **2.3 × 10⁻¹⁷** in the first iteration, a gap below what double-precision
distance arithmetic reliably resolves. Exact arithmetic puts them in cluster 5, as
this code does; scikit-learn put them in cluster 0, and the two trajectories reached
different fixed points from there. The check is therefore made against exact
arithmetic, which has no rounding, and not against another library. The silhouette
reported for this partition matches scikit-learn's own function on the same
partition to nine places (0.397757519).

**3. Normalisation against numpy**, on the real rows (largest difference `0.00e+00`).
The seed makes a case the ADR only describes: **every customer has exactly 10
purchases**, so frequency is a constant feature. It maps to 0 for all 30, and a naive
min-max would have divided 0 by 0.

## The acceptance criteria, one by one

**A completed K-means run records its method and its parameters.** A run written
through the pipeline (with the stand-in mapping):

```
run 31: method=KMEANS window_days=180 customer_count=30
recorded parameters: k=6, seed=2026, max_iterations=100, tolerance=0.0001, window_days=180,
  initialisation="k-means++, seeded",
  normalisation="min-max to [0, 1] across the run; recency reversed so higher is better; a constant feature is 0",
  empty_cluster_policy="an emptied cluster takes the customer farthest from their own cluster's centre, from a cluster holding at least two; lowest customer id on a tie",
  tie_break="a customer equidistant from two clusters goes to the lower-numbered one"
```

**Quality measures are recorded alongside them:**

```
recorded quality: {"inertia": 0.541445, "converged": true, "iterations": 4, "silhouette": 0.397758,
  "stopped_on": "tolerance", "final_shift": 0.0, "cluster_sizes": [7, 6, 6, 5, 4, 2],
  "customers_clustered": 30, "customers_unassigned": 0, "empty_cluster_events": 0}
```

**The same data and seed give the same partition.** Repeated, and with the rows
handed over in reverse order (the database may return them in any order, and the fit
sorts by customer id first):

```
same seed, same rows      : True
same seed, rows reversed  : True
another seed (7)          : converged True | inertia 0.551869 vs 0.541445
```

**An empty cluster is refilled, recorded, and never leaves fewer than k clusters.**
Six customers with identical purchases were added in a transaction that was rolled
back, so that only they and one other customer had sales in a 1-day window, and the
fit was asked for k = 4. Identical customers leave no room for four distinct centres,
which is how an empty cluster genuinely arises:

```
customers clustered=7 k=4 cluster_sizes=[4, 1, 1, 1] empty_cluster_events=2
k clusters, none empty; refills recorded on the run
```

**A fit that reaches the iteration limit says so and is not presented as converged.**
The same customers, allowed one iteration and a tolerance of zero:

```
kmeans_not_converged window_days=180 iterations=1 final_shift=0.124747
run 32: converged=False stopped_on=iteration_limit iterations=1 final_shift=0.124747
```

The run is still written, recorded as not converged, and a warning is logged.

**Too few customers to fill k clusters is refused and writes nothing.** A 1-day
window in which one customer has sales, with k = 6:

```
refused: Only 1 customer has sales in the window; k=6 needs at least 6.
runs before/after: 32 32
```

**A customer equidistant from two centroids goes to the lower-numbered cluster,**
stated on the run (`tie_break` above) and pinned by tests that put a customer exactly
between two clusters and require the same answer on every fit.

**No scientific-computing dependency.** ADR-0021's two commands, run as written:

```
$ grep -rniE 'scikit-learn|sklearn|^numpy|^scipy' web/requirements.txt web/requirements-dev.txt pyproject.toml
(prints nothing)

$ grep -nE '^\s*(import|from)\s+' web/services/kmeans.py | grep -vE '\b(web|typing|dataclasses|math|random|statistics|collections|itertools|decimal|datetime)\b'
(prints nothing)

$ grep -nE '^\s*(import|from)\s+' web/services/segmentation.py | grep -vE '<the same allowlist>'
23:from __future__ import annotations
25:import logging
26:import time
```

`kmeans.py` passes the ADR's check exactly. `segmentation.py` prints three lines, all of
them the standard library and none of them new: the ADR's list of allowed modules does
not include `logging`, `time` or `__future__`, which that file has imported since
F3-10. What the story asks, that it imports only the standard library and the
application, is met and is now a test (`psycopg`, which was a third-party import there,
is gone). Either the list should be widened the next time ADR-0021 is touched, or say
so and the run logging and timing can be moved out of the file.

## The tests catch a broken fit

Tests that all pass on the first run prove little, so faults were seeded into the code,
one at a time, and each was required to make a test fail:

| Fault seeded | Result |
|---|---|
| A tie goes to the *higher* centroid index | 1 test fails |
| A constant feature maps to 1 instead of 0 | 1 test fails |
| Recency is not reversed | 1 test fails |
| Empty clusters are never refilled | 1 test fails |
| The refill takes the *nearest* customer, not the farthest | 1 test fails |
| A fit that hit the limit claims to have converged | 1 test fails |
| Customers are not sorted by id first, so row order reaches the result | 1 test fails |
| The seed is ignored | 1 test fails |

## Tests

`tests/test_kmeans.py` (56) and `tests/test_kmeans_adapter.py` (22) are new.

```
$ pytest -q
1108 passed         # 1030 with F9-01, plus 78
$ black --check .   # All done
$ ruff check .      # All checks passed!
```

## Things to know

* **K-means assignments carry no business segment and no quintile scores.**
  `segment_id` and the three scores are empty; the raw recency, frequency and
  monetary values are recorded. Quintiles are `RFM_RULES`', and giving K-means
  customers some would make two measures look like one. The consequence is for
  **F9-03**: pages that list a segment's members read `segment_id`, so a K-means run's
  customers will not appear there until it decides how a label resolves to a segment.
  ADR-0018's compliance queries hold either way (both return no rows above).
* **The silhouette is skipped above 2,000 customers,** where it compares every customer
  with every other in pure Python, and is recorded as absent with the reason.
  Inertia and the sizes are always recorded.
* **`k` and `seed` have no default,** on purpose: a run that quietly chose either could
  not be reproduced from what it recorded.
* **On the seed, K-means sees a two-dimensional problem,** because frequency is
  constant. That is a property of the demonstration data and it is handled, not an
  error.

## Not evidenced here

The mapping from clusters to labels, and the refusal of a run whose k differs from the
label count (F9-03); a page or a form to start a K-means run; and the comparison of the
two methods' runs (F9-04).

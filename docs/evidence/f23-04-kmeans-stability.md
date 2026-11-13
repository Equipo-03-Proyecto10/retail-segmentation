# F23-04 (#816): K-means stability, and a registered result reproduced

Whether a customer's segment depends on luck. Every table is printed by
`python -m analysis.stability_study` (about three and a half minutes on the machine that
produced it, no database) over the F23-01 evaluation dataset, fingerprint
`8a73b618d299d8abf08fd22d2cd10901b8222a0ca536bf015a77378e44e3826a`. **All of it is
synthetic** (ADR-0052). The 3,979 customers with a sale in the 180-day window are
clustered with the production fit and production normalization (min-max, R reversed),
as in F23-03. Nothing here changes production code or a default. Governing records:
ADR-0021, RN-58.

## 1. A registered result is reproduced

RN-58 says a run is fixed by its seed, k, iteration limit, tolerance and feature window,
and that all five are stored on the run. The registered result is the F23-03 choice with
the main seed: k = 4, seed 11, 100 iterations, tolerance 0.0001, window 180 days, dataset
seed 20261031. The study rebuilds the customers and the window from those values, fits,
and does it three times, each from scratch.

| Registered result | Value |
|---|---|
| registered k, seed | 4, 11 |
| window, iteration limit, tolerance | 180 days, 100, 0.0001 |
| customers clustered | 3979 |
| inertia | 54.170765 |
| silhouette (sampled) | 0.582904 |
| cluster sizes | [1471, 1231, 748, 529] |
| iterations, converged | 6, True |
| partition digest (SHA-256) | 040e8e1a8171ac792d4e0c6235376b13a45602afa075f67d372a020e677d26e0 |
| fits repeated from scratch: 3 | identical |

"Identical" is checked three ways: every customer's cluster number, every centroid
coordinate, and the quality record a run stores (`recorded_parameters`: inertia,
silhouette, sizes, iterations, convergence). The digest is the SHA-256 of the customer
ids with their cluster numbers, and `tests/test_stability_study.py` pins it with the
inertia and the sizes, so a change that moves this result fails a test. A second test
shows that a changed partition would be reported and not assumed away.

## 2. Other seeds and bootstrap resamples

Two repeats of the fit, and one measure each.
- **Seeds:** the same customers fitted at 20 seeds (1 to 20). Measure: the **adjusted
  Rand index (ARI)** between every pair of fits (190 pairs), which is 1 for the same
  partition and about 0 for two unrelated ones, and does not depend on cluster numbers.
- **Bootstrap:** 20 resamples of the customers with replacement (resampling seed 7), the
  fit repeated on each (seed 11), and then every one of the 3,979 customers assigned to
  the nearest centroid of that fit. Measures: the ARI of that partition against the
  full-data fit, and, for each cluster of the full-data fit, the **Jaccard index** of its
  best match in the resample (Hennig's cluster-wise measure), averaged over the 20. The
  weakest cluster is the one to read: a partition is only as stable as its least stable
  group.

**Thresholds,** stated before the results and applied to every k. They are judgements,
like F23-03's commercial limits, and the owner may move them.

| Verdict | Rule |
|---|---|
| stable | seed ARI mean at least 0.80, bootstrap ARI mean at least 0.80, and weakest cluster at least 0.75 |
| poor | seed ARI mean below 0.60, or bootstrap ARI mean below 0.60, or weakest cluster below 0.60 |
| moderate | everything else: usable, but a different seed or sample may move customers |

| k | Seed ARI, mean of 190 pairs | Seed ARI, lowest | Fits with a cluster of one | Bootstrap ARI, mean of 20 | Bootstrap ARI, lowest | Weakest cluster (Jaccard) | Verdict |
|---|---|---|---|---|---|---|---|
| 2 | 1.00 | 1.00 | 0 of 20 | 0.99 | 0.99 | 1.00 | stable |
| 3 | 0.74 | 0.49 | 1 of 20 | 0.81 | 0.49 | 0.74 | moderate |
| 4 | 0.86 | 0.39 | 1 of 20 | 0.84 | 0.52 | 0.77 | stable |
| 5 | 0.77 | 0.57 | 2 of 20 | 0.77 | 0.51 | 0.05 | poor |
| 6 | 0.77 | 0.53 | 3 of 20 | 0.72 | 0.49 | 0.05 | poor |
| 7 | 0.76 | 0.58 | 3 of 20 | 0.80 | 0.65 | 0.05 | poor |
| 8 | 0.73 | 0.56 | 4 of 20 | 0.77 | 0.61 | 0.15 | poor |

Each cluster of the full-data fit, with its best-match Jaccard averaged over the 20
resamples and its size in customers:

| k | Jaccard of each cluster (its size in customers) |
|---|---|
| 2 | 1.00 (2080), 1.00 (1899) |
| 3 | 0.74 (660), 0.85 (1480), 0.89 (1839) |
| 4 | 0.77 (529), 0.87 (1471), 0.89 (1231), 0.84 (748) |
| 5 | 0.74 (528), 0.79 (1471), 0.80 (1231), 0.87 (748), 0.05 (1) |
| 6 | 0.64 (528), 0.68 (1471), 0.87 (768), 0.84 (621), 0.05 (1), 0.75 (590) |
| 7 | 0.87 (291), 0.77 (1062), 0.94 (768), 0.71 (621), 0.05 (1), 0.82 (590), 0.77 (646) |
| 8 | 0.85 (291), 0.72 (1062), 0.95 (702), 0.68 (347), 0.15 (1), 0.85 (539), 0.74 (646), 0.64 (391) |

Reading it:
- **k = 2 is perfectly stable** (it separates active from lapsed customers) and is
  outside the commercial limits (F23-03).
- **From k = 5 up the verdict is poor, for one reason.** The cluster of one customer, the
  extreme spender of F23-02 (the same customer at every k, with M at its maximum), is
  recovered in only about one resample in twenty (mean Jaccard 0.05, 0.15 at k = 8).
  Every other cluster at those k is between 0.64 and 0.95. This is the cluster F23-03
  excluded by size, now shown to be unstable too.
- **k = 3 is moderate:** its smallest cluster (660 customers) is found again only at 0.74
  and one seed in twenty ends with a cluster of one.
- **k = 4 is stable by the thresholds, with one exception that matters.** At seed 19 the
  sizes are (1999, 1232, 747, 1): the extreme spender ends alone in a cluster, and the
  fit is otherwise a different partition. The other 19 seeds agree closely, and that one
  fit is why the lowest pair ARI is 0.39. Seed 19 does the same at k = 3. The registered
  seed, 11, is not affected (section 1). A run's quality record already holds the cluster
  sizes, so such a run is recognisable: its smallest cluster is under the 2% limit of
  F23-03.

## 3. Adjacent time windows

The same fit (k, seed 11) over a 180-day feature window ending 0, 30, 60, ... 180 days
back, seven windows, min-max recomputed inside each. Adjacent windows share 150 of their
180 days. Two fits are compared over the customers both have, by three measures:
- **ARI** between the two partitions.
- **Customers changing rank:** the share whose cluster has a different place in the
  best-to-worst order (RN-38), which means the same thing in two fits where a cluster
  number does not.
- **Centroid movement:** the distance between the centroids of the same rank, in the
  normalized R, F, M space (each between 0 and 1), averaged over the k centroids.

The last two columns are the same measures for the same window fitted at another seed
(12 to 16, mean of five), which is movement that is only noise. The "disjoint" columns
compare the window ending today with the one 180 to 360 days back, which share no sale.

| k | ARI, 30-day steps (mean of 6) | Customers changing rank | Centroid movement (mean) | ARI, disjoint windows | Customers changing rank | ARI, other seeds only (mean of 5) | Customers changing rank |
|---|---|---|---|---|---|---|---|
| 2 | 0.51 | 14.1% | 0.061 | -0.01 | 47.7% | 1.00 | 0.0% |
| 3 | 0.50 | 26.7% | 0.085 | 0.10 | 62.0% | 0.79 | 19.8% |
| 4 | 0.47 | 35.7% | 0.080 | 0.11 | 66.7% | 0.84 | 11.3% |
| 5 | 0.40 | 55.4% | 0.244 | 0.17 | 78.6% | 0.77 | 43.4% |
| 6 | 0.41 | 50.3% | 0.165 | 0.11 | 81.1% | 0.73 | 22.7% |
| 7 | 0.41 | 57.1% | 0.172 | 0.10 | 85.1% | 0.79 | 50.0% |
| 8 | 0.42 | 60.6% | 0.143 | 0.09 | 86.5% | 0.76 | 50.0% |

Reading it:
- **The clusters move far more with time than with the seed.** At k = 4, shifting the
  window by 30 days gives an ARI of 0.47 and moves 35.7% of customers to another rank,
  against 0.84 and 11.3% for a change of seed. The centroids themselves move about 0.08
  in a space of side 1, so the groups stay in the same place while the customers inside
  them change. At k = 2 the movement is smaller (14.1% change rank).
- **Windows with no sale in common agree barely better than chance** (ARI between -0.01
  and 0.17): what a customer did in the last six months says little about the six months
  before.
- This is movement of the customers, not of the model: a segment is a statement about
  the window it was fitted on. It is the movement F22's migration matrix is built to
  show, and it is the reference against which a run-to-run change can be read.

## 4. Feedback to F23-03's choice of k

The study ends with poor stability for k = 5 to 8 and a moderate verdict for k = 3, so
the choice made in F23-03 was checked against it, and the result is written into that
document (section 6) as well as here.
- **k = 4 stands.** It is the only k inside the commercial limits with a stable verdict;
  k = 3 is moderate (weakest cluster 0.74, seed ARI mean 0.74 against 0.86). F23-03's
  five-seed figures (0.69 at k = 3, 0.79 at k = 4) and these 20-seed ones give the same
  order.
- **k = 5 to 8 are excluded twice:** by F23-03's smallest-cluster limit and now by
  stability. A second normalization (log or rank, F23-02) would remove the cluster of
  one; in that case this study must be repeated for the k it opens, because the poor
  verdict here comes from that cluster.
- **A condition on k = 4:** one seed in twenty (seed 19) ends with a cluster of one.
  An administrator who runs K-means with k = 4 should treat a run whose smallest cluster
  is under 2% of customers as a failed fit and run it again with another seed. This
  document recommends that and does not add the check: the fit still takes k and the seed
  from the administrator and writes what it finds.

## 5. Limits of this study

- Synthetic data with six planted groups. The size of the time movement in section 3 is a
  property of how the generator plants behaviour (relative to the end date), and real
  customers may move more or less. The method carries over; the numbers do not.
- 20 seeds and 20 resamples are enough to read means, not extremes: each "lowest" column
  is one fit, and seed 19 shows how a single fit can set it.
- The bootstrap resamples customers but keeps the min-max scaling of the full
  population, as production would, so what it measures is the sampling of customers, not
  the scaling's sensitivity to who is in the sample.
- ARI and Jaccard are the stated measures and the thresholds are judgements. The silhouette
  is not used here (it is F23-03's).
- The sampled silhouette of the registered fit (0.582904) comes from a 1,000-customer
  sample seeded by the fit's seed, so it reproduces with the fit.

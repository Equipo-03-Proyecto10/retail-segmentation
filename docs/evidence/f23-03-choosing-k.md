# F23-03 (#815): choosing k, and the silhouette past 2,000 customers

How many segments the K-means run should produce, by a procedure that can be repeated,
and what the run records when it has too many customers for an exact silhouette. Every
table is printed by `python -m analysis.k_study` (about a minute on a Raspberry Pi 5, no
database) over the F23-01 evaluation dataset, fingerprint
`8a73b618d299d8abf08fd22d2cd10901b8222a0ca536bf015a77378e44e3826a`. **All of it is
synthetic** (ADR-0052). The 3,979 customers with a sale in the 180-day window are
clustered with the production fit and production normalization (min-max, R reversed),
so this judges the model as deployed. Governing records: ADR-0021, ADR-0030, RN-38, RN-58.

## 1. The procedure

For each k from 2 to 12, fit at five seeds (11 to 15) and report:
- **Inertia**, which always falls as k grows, so it shows an elbow and never a minimum.
- **Silhouette**, higher is better. 3,979 customers is past `SILHOUETTE_MAX_CUSTOMERS`
  (2,000), so it is the **sampled** silhouette: 1,000 customers drawn by a seeded
  partial shuffle (the fit's seed), each compared with every customer. The run records
  `silhouette_sampled`, `silhouette_sample_size` and `silhouette_sample_seed` beside the
  value, and the old "skipped" text now appears only for k = 1. The mean over five seeds
  shows how much a seed moves it.
- **Smallest cluster**, as a count and share.
- **Seed stability**, the mean pairwise adjusted Rand index between the five fits.
- **ARI against the planted groups**, with the F23-01 caveat that these are what was
  generated, not what a model should find.
- **Commercial limits**, stated here and checked in code (`MIN_SEGMENTS`,
  `MAX_SEGMENTS`, `MIN_SHARE`): at least 3 segments (two cannot separate valuable,
  at-risk and lost customers), at most 8 (a team can run a distinct treatment for about
  that many), and no segment under 2% of customers (too small for an experiment arm or
  a campaign). They are business judgements, not measurements, and the owner may move them.

Dataset seed 20261031, fit seeds 11 to 15.

## 2. Results

| k | Inertia | Silhouette (sampled) | Silhouette, mean of 5 seeds | Smallest cluster | Seed stability (ARI) | ARI vs planted | Within commercial limits |
|---|---|---|---|---|---|---|---|
| 2 | 161.3 | 0.648 | 0.646 | 1899 (47.7%) | 1.00 | 0.36 | no |
| 3 | 104.5 | 0.566 | 0.566 | 660 (16.6%) | 0.69 | 0.41 | yes |
| 4 | 54.2 | 0.583 | 0.570 | 529 (13.3%) | 0.79 | 0.65 | yes |
| 5 | 53.1 | 0.583 | 0.548 | 1 (0.0%) | 0.74 | 0.65 | no |
| 6 | 44.7 | 0.538 | 0.508 | 1 (0.0%) | 0.88 | 0.53 | no |
| 7 | 30.4 | 0.510 | 0.478 | 1 (0.0%) | 0.77 | 0.55 | no |
| 8 | 27.0 | 0.488 | 0.453 | 1 (0.0%) | 0.71 | 0.53 | no |
| 9 | 23.1 | 0.468 | 0.441 | 1 (0.0%) | 0.71 | 0.54 | no |
| 10 | 22.3 | 0.469 | 0.434 | 1 (0.0%) | 0.73 | 0.54 | no |
| 11 | 19.9 | 0.452 | 0.424 | 1 (0.0%) | 0.65 | 0.51 | no |
| 12 | 18.1 | 0.428 | 0.417 | 1 (0.0%) | 0.72 | 0.46 | no |


Reading it:
- **k = 2 has the best silhouette** (0.648) and perfect stability, because it splits
  active from lapsed customers. It is outside the commercial limits and has an ARI
  against the planted groups of 0.36.
- **From k = 5 up the smallest cluster is one customer.** Min-max scaling lets an extreme
  spender (F23-02, section 2) become a cluster of one, which no label can describe and
  no campaign can use. This is the F23-02 finding seen from the k side, and it excludes
  every k from 5 to 12 under the commercial limit.
- **Of the k that pass the limits, k = 4 is better than k = 3:** a higher silhouette
  (0.583 against 0.566), stability 0.79 against 0.69, and an ARI with the planted groups of
  0.65 against 0.41. Inertia drops by half between 3 and 4 (104.5 to 54.2) and barely
  moves from 4 to 5 (54.2 to 53.1): that is the elbow.

## 3. The choice

**k = 4 under the model as deployed (min-max).** It is the only k that is inside the
commercial limits, has the elbow, and has the best agreement with the planted groups.
k = 6, the size of the label vocabulary, is not recommended with min-max: its smallest
cluster is one customer, its stability (0.88) comes from that same outlier cluster being
found every time, and its ARI with the planted groups (0.53) is below k = 4.
If a later model version changes the normalization to log or rank, which F23-02
studied, the extreme spender no longer forms a cluster, and this table must be
regenerated, because k = 6 then becomes available without a singleton. This document
does not change any default: a run still takes k from the administrator, and there is
no default k (`KMeansParams`).

## 4. Which labels the chosen k can produce

ADR-0030 pairs the clusters, ordered best to worst, with the six labels by proportional
rank: position `i` of `k` takes label `i * (6 - 1) / (k - 1)`, rounded to the nearest,
halves towards the worse label. The pairing is computed by `describe_mapping`, and the
run records it as `label_mapping`.

| k | Labels produced | Labels no cluster takes | Shared |
|---|---|---|---|
| 2 | CHAMPION, LOST | LOYAL, POTENTIAL, AT_RISK, HIBERNATING | none |
| 3 | CHAMPION, AT_RISK, LOST | LOYAL, POTENTIAL, HIBERNATING | none |
| 4 | CHAMPION, POTENTIAL, AT_RISK, LOST | LOYAL, HIBERNATING | none |
| 5 | CHAMPION, LOYAL, AT_RISK, HIBERNATING, LOST | POTENTIAL | none |
| 6 | CHAMPION, LOYAL, POTENTIAL, AT_RISK, HIBERNATING, LOST | none | none |
| 7 | CHAMPION, LOYAL, POTENTIAL, AT_RISK x2, HIBERNATING, LOST | none | AT_RISK |
| 8 | CHAMPION, LOYAL x2, POTENTIAL, AT_RISK, HIBERNATING x2, LOST | none | LOYAL, HIBERNATING |
| 9 | CHAMPION, LOYAL x2, POTENTIAL, AT_RISK x2, HIBERNATING x2, LOST | none | LOYAL, AT_RISK, HIBERNATING |
| 10 | CHAMPION, LOYAL x2, POTENTIAL x2, AT_RISK x2, HIBERNATING x2, LOST | none | LOYAL, POTENTIAL, AT_RISK, HIBERNATING |
| 11 | CHAMPION, LOYAL x2, POTENTIAL x2, AT_RISK x2, HIBERNATING x2, LOST x2 | none | LOYAL, POTENTIAL, AT_RISK, HIBERNATING, LOST |
| 12 | CHAMPION x2, LOYAL x2, POTENTIAL x2, AT_RISK x2, HIBERNATING x2, LOST x2 | none | CHAMPION, LOYAL, POTENTIAL, AT_RISK, HIBERNATING, LOST |


For k = 4: **CHAMPION, POTENTIAL, AT_RISK and LOST**. LOYAL and HIBERNATING take no
cluster, so no customer is ever assigned them, and **no two clusters share a label**.
Sharing starts at k = 7. A reader who expects a customer in every one of the six
labels needs k = 6 exactly, which is the trade-off in section 3: the vocabulary has six
labels, and the data, scaled as it is now, supports four groups. This is the mismatch
ADR-0030 accepted knowingly (RN-38); this document states its consequence for the
chosen k and does not revise it.

## 5. Limits of this study

- Synthetic data with six planted groups: a real customer base may have a different
  elbow. Re-run the study on real data before relying on k = 4.
- The sampled silhouette is an estimate. Over 1,000 customers its standard error is
  roughly 0.01, which is why k = 4 and k = 5 (both 0.583 on the main seed) are not
  separated by silhouette, but by the smallest cluster and the mean over seeds.
- The cost of a sampled silhouette is linear in the population (1,000 customers each
  compared with all): about 4 million distance calculations at this size.

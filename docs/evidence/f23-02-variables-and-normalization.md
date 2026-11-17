# F23-02 (#814): variables, normalization and extreme values

Why K-means clusters over R, F and M, why min-max, and what a few extreme spenders do
to it. Every table below is printed by `python -m analysis.normalization_study`
(about 25 s on a Raspberry Pi 5, no database, no numpy) over the F23-01 evaluation
dataset, fingerprint `8a73b618d299d8abf08fd22d2cd10901b8222a0ca536bf015a77378e44e3826a`
(seed 20261031). **All of it is synthetic** (ADR-0052). The 3,979 customers with a sale
in the 180-day window are clustered, 36 of them extreme spenders (25 times their
group's ticket). The production fit, `web.services.kmeans.fit`, is used unchanged: only
the points handed to it differ.

Yardsticks. Cluster sizes and seed stability (mean pairwise adjusted Rand index, ARI,
between fits at nine seeds) need no labels. ARI against the planted groups is a second
view, with the F23-01 caveat that the planted names are what was generated, not what a
model should find. k is 6 and the main seed is 11, as in the web's run.

## 1. Variables

R, F and M are the definition of the model (RN-58, ADR-0018) and are kept. The
candidates come from the catalog and the channels: average ticket, distinct categories,
distinct stores, the share of sales in the top category, and the share in each channel.
The rule is written in code (`decide`): a candidate is dropped when it tracks R, F or M
(|Spearman rho| of 0.8 or more) or when under 10% of its variance lies between planted
groups (eta squared); otherwise it is a candidate for a later model version.

| Variable | eta2 by planted group | rho with F | rho with M | rho with R | Decision |
|---|---|---|---|---|---|
| R (recency, days) | 0.97 | -0.72 | -0.66 | +1.00 | kept (R, F, M are the definition) |
| F (frequency) | 0.77 | +1.00 | +0.95 | -0.72 | kept (R, F, M are the definition) |
| M (monetary) | 0.14 | +0.95 | +1.00 | -0.66 | kept (R, F, M are the definition) |
| average ticket (M / F) | 0.03 | +0.57 | +0.77 | -0.37 | dropped: does not separate behaviour |
| distinct categories | 0.69 | +0.96 | +0.91 | -0.68 | dropped: redundant with R, F or M |
| distinct stores | 0.23 | +0.53 | +0.50 | -0.39 | candidate: separates behaviour on its own |
| top-category share | 0.51 | -0.77 | -0.74 | +0.56 | candidate: separates behaviour on its own |
| channel 1 share | 0.35 | +0.38 | +0.32 | -0.55 | candidate: separates behaviour on its own |
| channel 2 share | 0.18 | +0.35 | +0.31 | -0.43 | candidate: separates behaviour on its own |
| channel 3 share | 0.25 | -0.01 | +0.02 | +0.25 | candidate: separates behaviour on its own |
| channel 4 share | 0.04 | +0.19 | +0.17 | -0.11 | dropped: does not separate behaviour |
| channel 5 share | 0.13 | +0.14 | +0.16 | +0.10 | candidate: separates behaviour on its own |

Reading it:
- **Dropped, with reasons.** Average ticket is derived from M and F and barely differs between groups (0.03). Distinct categories is almost F (rho 0.96). Channel 4's share carries nothing (0.04).
- **Not adopted despite passing.** Store count, top-category share and channels 1, 2, 3 and 5 separate groups, but they do so because the generator gave each group its own category and channel weights. That is what was planted, so it shows the variables can carry signal, not that real customers behave that way. They also change the model's definition (RN-58 names R, F, M), and `kmeans._means` is written for three axes. Adding one is a model version (see section 4), and it needs evidence on real data before anyone proposes it.
- **M separates groups weakly in raw terms (eta2 0.14)** because the extreme spenders dominate its variance, which is the subject of the next sections.

## 2. Normalization

Min-max scaling puts the largest value at 1. With one customer at 513,760 against a
median of 1,849, almost everyone sits at the bottom of M:

| Variable | min-max median | min-max p99 | log median | log p99 | Maximum |
|---|---|---|---|---|---|
| F | 0.1250 | 0.7917 | 0.357 | 0.917 | 25 vs median 4 |
| M | 0.0035 | 0.0406 | 0.395 | 0.656 | 513,760 vs median 1,849 |

Four normalizations were fitted. Robust is `(x - median) / IQR`, unbounded; percentile
rank is added as the limiting case of outlier resistance.

| Method | Cluster sizes (k=6, seed 11) | ARI planted | ARI planted, mean of 9 seeds | Seed stability (mean pairwise ARI) | Smallest cluster, any seed | Fits with a cluster under 1% |
|---|---|---|---|---|---|---|
| min-max (production) | 1471 / 768 / 621 / 590 / 528 / 1 | 0.527 | 0.571 | 0.730 | 1 | 1 of 9 |
| log1p + min-max | 781 / 672 / 667 / 653 / 621 / 585 | 0.517 | 0.527 | 0.808 | 17 | 1 of 9 |
| robust (median / IQR) | 2232 / 1365 / 366 / 12 / 3 / 1 | 0.275 | 0.298 | 0.860 | 1 | 9 of 9 |
| percentile rank | 724 / 701 / 700 / 641 / 622 / 591 | 0.403 | 0.415 | 0.758 | 16 | 1 of 9 |

- **Min-max, as shipped,** spends a cluster on one customer in the main fit (and in 1 of 9 seeds), and the other five clusters are lopsided (1471 against 528).
- **Robust is the worst here, and that is the finding.** Dividing by the IQR leaves the extremes thousands of units away, so K-means gives them their own clusters (12, 3 and 1 customers) in every seed. Its high stability is the stability of that mistake. Robust scaling resists outliers in the centre of the scale, not in the geometry K-means uses.
- **Log1p** compresses the extremes, gives balanced sizes (585 to 781) and better stability than min-max (0.808 against 0.730), at an ARI against the planted groups that is a point lower. It still has a small cluster in one seed (17).
- **Percentile rank** also balances sizes but discards distances, and agrees least with the planted groups of the bounded methods (0.403).

**Chosen: log1p**, over min-max, robust and rank. It is the only candidate that removes the compression without throwing away the shape of the data.

## 3. Extreme values

Extreme spenders are 36 of 3,979 (0.9%). Treatments, all followed by the min-max the
production fit uses:

| Treatment | Cluster sizes | Extreme spenders in their commonest cluster | Clusters holding any | ARI planted | ARI planted, mean of 9 seeds | Seed stability | Smallest cluster, any seed | Fits with a cluster under 1% |
|---|---|---|---|---|---|---|---|---|
| none (min-max) | 1471 / 768 / 621 / 590 / 528 / 1 | 12 of 36 | 6 | 0.527 | 0.571 | 0.730 | 1 | 1 of 9 |
| winsorize F, M at p99 | 1235 / 915 / 734 / 676 / 214 / 205 | 14 of 36 | 5 | 0.700 | 0.685 | 0.895 | 23 | 3 of 9 |
| winsorize F, M at p95 | 1235 / 941 / 709 / 708 / 365 / 21 | 21 of 36 | 3 | 0.693 | 0.652 | 0.879 | 20 | 2 of 9 |
| transform: log1p | 781 / 672 / 667 / 653 / 621 / 585 | 15 of 36 | 5 | 0.517 | 0.527 | 0.808 | 17 | 1 of 9 |
| transform + winsorize p99 | 805 / 670 / 664 / 664 / 627 / 549 | 12 of 36 | 5 | 0.529 | 0.528 | 0.795 | 282 | 0 of 9 |

- **None** is the baseline above: the extreme spender is not spread evenly, 12 of the 36 share a cluster and the rest scatter across all six, one of them alone.
- **Winsorizing alone** improves agreement with the planted groups most (ARI 0.70) and stability (0.895), because it keeps linear distances for everyone else. But it leaves tiny clusters in 3 of 9 seeds at p99 and 2 of 9 at p95, and it moves where the clusters are (214 and 205 where there were 528 and 590). A cap at p95 changes ordinary customers, not only outliers.
- **Log1p alone** fixes the main fit but not all seeds (17 in one).
- **Log1p, then a cap at p99** is the only treatment with no cluster under 1% in any of the nine seeds (smallest 282), with sizes between 549 and 805 and the same ARI as plain min-max. It costs some of the stability that winsorizing alone gets.

**Decision on extremes: transform and then winsorize at p99.** The reason is the failure
that matters to a store manager, a cluster that is one person, and this is the only
treatment that never produced one. Its price is stated, not hidden: its agreement with
the planted groups is not better than today's (0.529 against 0.527), and winsorizing
alone agrees more (0.70) while tolerating a tiny cluster in a third of seeds. Which of
the two matters more is F23-06's decision about criteria.

## 4. What this recommends

Nothing here changes production. The recommendation is a proposal for a new model
version under F22-02 (ADR-0064: a model version is immutable, and a change of version
is never reported as behaviour):

> `kmeans` version N+1: features R, F, M unchanged; `log1p` of each, a cap of F and M
> (after the log) at the 99th percentile of the run, then min-max with R reversed, as
> today.

- The current version (min-max, no treatment) stays as it is, and runs under it stay comparable to each other.
- ADR-0018 is not edited. It is already superseded by ADR-0030, and the normalization it fixed would be superseded by a new ADR written when the version is accepted, not by an edit.
- The cap is computed per run from the data, so the version must record that rule and not a number, or two runs would not be comparable.

## Limits

- One synthetic dataset, one seed for the data, k fixed at 6. The planted groups share the generator's own assumptions (lognormal-like tickets, 25 times extremes), so the numbers say how the methods behave on this shape, not how real customers will. F23-03 and F23-04 test k and stability, and the real data does not exist yet.
- Silhouette is absent: above `SILHOUETTE_MAX_CUSTOMERS` the production code skips it, and silhouettes in different scalings are not comparable anyway.
- Eta squared and Spearman are screens, not tests of what a variable adds inside a fit; the fit supports three axes only.
- Nine seeds bound "never" loosely: 0 of 9 is not the absence of a failure.

## Reproduce

```bash
python -m analysis.normalization_study
```

The output is deterministic. `tests/test_normalization_study.py` pins the helpers and
runs the pipeline on 600 customers.

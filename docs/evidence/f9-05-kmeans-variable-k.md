# F9-05 — K-means with any k, paired with the stable labels by proportional rank (#336)

Evidence that a K-means run with `k` other than the six labels of `segment_label`
is run rather than refused, that its clusters take stable labels by ADR-0030's
documented rule, that the run records which labels were shared or left unused, and
that every page downstream still reads only stable labels (ADR-0018, ADR-0030).

Covers the two acceptance criteria of #336 and RN-38 as revised. No schema change:
the pairing is recorded in `segmentation_run.parameters`, a column that already
held each run's parameters.

## How it was produced

A throwaway PostgreSQL 16 container loaded with the three scripts in order, and two
real K-means runs over the seed through `web.services.segmentation.run_kmeans` as
`retail_app`: run **#31** with `k = 5` and run **#32** with `k = 8`, both seed 7
over 180 days. The application was then run locally against that database, signed
in as the seeded demonstration administrator, and captured with Playwright
(Chromium) at 375 px and 1440 px, full page. No page overflowed the viewport
horizontally.

## What the two runs recorded

| Run | k | Labels by rank, best first | Shared | Without a cluster | Customers per label |
|---|---|---|---|---|---|
| #31 | 5 | CHAMPION, LOYAL, AT_RISK, HIBERNATING, LOST | — | POTENTIAL | CHAMPION 4, LOYAL 6, AT_RISK 5, HIBERNATING 7, LOST 8 |
| #32 | 8 | CHAMPION, LOYAL, LOYAL, POTENTIAL, AT_RISK, HIBERNATING, HIBERNATING, LOST | LOYAL, HIBERNATING | — | CHAMPION 2, LOYAL 9, POTENTIAL 4, AT_RISK 2, HIBERNATING 9, LOST 4 |

ADR-0030's three database checks returned **0** rows after both runs: no history
label outside the vocabulary, no scored customer without a label, and no key of
`label_mapping.clusters_per_label` that is not a label code.

## Downstream pages read only stable labels

With both runs in place, each of these answered 200 as an analyst and its text
named no cluster number: run history for #31 and #32, the segmentation dashboard
for #31 and #32, the migration matrix #31 → #32, a migration explanation #31 → #32,
the segment history report for #32, a customer's timeline and segment change for
#32, the model comparison with and without runs chosen, recommendations, the
customer list and the consumption reports.

## What each screenshot shows

| Screenshot | Acceptance criterion | What to look for |
|---|---|---|
| `f9-05-form-k5-{375,1440}.png` | 1 | The K-means form with K = 5 and its hint: 2 or more, paired with the labels by rank (ADR-0030). |
| `f9-05-confirm-k5-{375,1440}.png` | 1 | K = 5 accepted and asking for confirmation, where it used to be refused. |
| `f9-05-run-detail-k8-shared-labels-{375,1440}.png` | 2 | Run #32: *8 clusters over 6 labels, paired by rank (ADR-0030)*, the label of each position, and *Shared: LOYAL (2 clusters), HIBERNATING (2 clusters)*. Every customer row carries a label code. |
| `f9-05-dashboard-k5-unused-label-{375,1440}.png` | 1, 2 | Run #31 on the segmentation dashboard: POTENTIAL at 0 customers, not omitted (RN-41). Its migration flow against the previous run (#30, RFM_RULES) also shows the cost ADR-0030 records: labels from a different model move customers who did not change. |
| `f9-05-matrix-k5-to-k8-{375,1440}.png` | 2 | The migration matrix from run #31 to run #32, keyed only by label, including POTENTIAL, which #31 left empty. |

## Checks

- `pytest`: every test passes. `black --check .` and `ruff check .`: clean.
- The pairing table for k = 2, 4, 5, 6, 8; `k = V` equal to ADR-0018's one-to-one
  pairing; best to best, worst to worst and order kept for every k from 2 to 14 over
  vocabularies of 1 to 8 labels; the half towards the worse label; and cluster
  renaming leaving labels unchanged are in `tests/test_cluster_labels.py`. Full runs
  with k = 5, 6 and 8 over the six labels, and the refusal of k = 1 before any sale
  is read, are in `tests/test_kmeans_run.py`; the page, form and run detail in
  `tests/test_segment_run_kmeans.py` and `tests/test_run_history.py`; ADR-0030's
  compliance cases in `tests/test_segmentation_pipeline.py`.

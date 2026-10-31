# F23-01 (#807): the evaluation dataset

A dataset large enough to judge a model, reproducible from a seed, with its behaviour
known in advance. The code and the description of what is planted are in
[`analysis/`](../../analysis/README.md). This records what it produced on 2026-10-31,
on PostgreSQL 18.6, the instance's major. **All of it is synthetic** (ADR-0052).

## The dataset

| Measure | Value |
|---|---|
| Seed and size | 20261031, 5,000 customers |
| Sales | 51,954 over 365 days |
| Fingerprint | `8a73b618d299d8abf08fd22d2cd10901b8222a0ca536bf015a77378e44e3826a` |
| Customers with a sale in the 180-day window | 3,979 |
| Customers with none, so unassigned (RN-21) | 1,021 |
| Extreme spenders | 46 |
| Generation | 0.3 s |

Python 3.12 and 3.14 gave the same fingerprint. The size is justified by two things:
each group holds at least 400 customers, so a share within a group is known to about
five points at 95%, and the 3,979 in the window pass `SILHOUETTE_MAX_CUSTOMERS`, so
the studies have to deal with that limit.

| Planted group | Customers | In the window | Median purchases | Median spend |
|---|---|---|---|---|
| `CHAMPION` | 400 | 400 | 15 | 14,279 |
| `LOYAL` | 850 | 850 | 8 | 3,865 |
| `POTENTIAL` | 750 | 750 | 4 | 1,314 |
| `AT_RISK` | 750 | 750 | 5 | 2,694 |
| `HIBERNATING` | 1,000 | 1,000 | 2 | 594 |
| `LOST` | 1,250 | 229 | 1 | 190 |

## It loads, and the existing code runs on it

Over the three SQL scripts, on a throwaway database, as the web's own role for the
runs:

| Step | Result |
|---|---|
| `python -m analysis.load` | 5,000 customers and 51,954 sales in 7.7 s, under every constraint |
| A URL on another host | Refused, exit 1, nothing connected |
| `segmentation.run`, RFM_RULES, 180 days | 0.8 s: 5,030 processed, 4,009 assigned, 1,021 unassigned, `data_origin` SEEDED |
| `segmentation.run_kmeans`, k=6, seed 11 | 1.2 s: 5,030 processed, 4,009 assigned, `data_origin` SEEDED, `silhouette_skipped: more than 2000 customers` |

The 30 extra customers in those runs are the seed's. Both runs are marked `SEEDED`, so
the web shows them as Synthetic. `tests/test_evaluation_dataset.py` repeats the load
inside a rolled-back transaction, with `SET CONSTRAINTS ALL IMMEDIATE`.

## Leads for the studies

These came out of checking the dataset. They are observations from one configuration,
not conclusions, and F23-02 to F23-06 have to test them.

- **Extreme spenders flatten M.** After min-max over the window, the median customer's M is 0.0035 and the 99th percentile's is 0.0406, because the maximum is 513,760 against a median of 1,849. For most customers M barely counts.
- **A cluster can be spent on one person.** Over 50 seeds at k=6, three fits gave a cluster of a single customer, and one gave a cluster of four. The other 46 had a smallest cluster of at least 153. So the outcome depends on the seed. The web's run at seed 11, which also holds the seed's 30 customers and measures from `now()`, had no such cluster.
- **`AT_RISK` barely appears under the seeded bands.** RFM_RULES gave that label to 4 of the 3,979 evaluation customers in the window, although 750 were planted in the group of that name. The label's bands ask for R 1 to 2 with F 3 to 5, so the band definitions and the quintiles need reading together before anyone concludes either is wrong.
- **Silhouette is absent at this size,** which F23-03 has to resolve.

A label matching a planted group is not the measure of a model: the group names are
only what was planted. Agreement was 1,438 of 3,979 for RFM_RULES and 1,137 of 3,979
for K-means, and it says nothing about which is better. F23-06 states the criteria.

No public dataset was used.

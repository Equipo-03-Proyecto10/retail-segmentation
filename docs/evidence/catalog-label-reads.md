# Catalog pages read the label, so K-means assignments are not shown as Unassigned

Fix for #272, found while reviewing F9-03 (#271) and due before or with F9-04
(#217). A `KMEANS` run writes a label and no `segment_id`, which ADR-0018
intends. The catalog pages read `segment_id`, so every customer K-means labelled
showed as *Unassigned*. They now read the label.

| Page | Before | After |
|---|---|---|
| Customer detail | *Current segment*: the segment the open row names, else *Unassigned* | *Current label*: the label the open row carries, whichever method wrote it, else *Unassigned*. *Rule-based segment* is added, with its link, when the row names one |
| Customer list | *Segment* column, joined on `h.segment_id` | *Label* column, joined on `h.label_code` |
| Segment detail | Lists the customers whose open row names the segment | Unchanged, and now says so: a segment is rule-based, and a K-means assignment is not listed under one |

Only a NULL label means *Unassigned* (RN-21). No schema change, no new
configuration, and nothing written.

## How this run was produced

PostgreSQL 16.2 on the local machine, loaded from empty with the three scripts in
order. The only difference from the repository was one line: this local build
has no `pg_trgm`, so `CREATE EXTENSION pg_trgm` was left out of `00`, as in
earlier local runs. The application ran under gunicorn as the restricted role
`retail_app`, in two copies: `develop` at `53819c8` for *before*, and this branch
for *after*. The pages were opened in headless Chromium, signed in as the seeded
ANALYST `user14@…`.

Runs were started by the application's own code, in this order:

```
run 31  KMEANS     run_kmeans(conn, 180, KMeansParams(k=6, seed=42))  processed=30 assigned=30
run 32  RFM_RULES  run_method(conn, "RFM_RULES", 180)                 processed=30 assigned=30
```

After run 31, every open row carried a label and no segment, as #272 reproduces:

```
open rows: KMEANS  label and no segment = 30, no label = 0, total = 30
Demo Customer 1  (…0001)  segment_id = NULL  label_code = POTENTIAL
```

## After a K-means run

| | |
|---|---|
| Customer detail, **before**: *Unassigned* | [`catalog-label-before-customer-1440.png`](catalog-label-before-customer-1440.png) |
| Customer detail, after: *Current label: Potential*, no segment row | [`catalog-label-kmeans-customer-1440.png`](catalog-label-kmeans-customer-1440.png) · [375 px](catalog-label-kmeans-customer-375.png) |
| Customer list, after: each customer's label | [`catalog-label-kmeans-list-1440.png`](catalog-label-kmeans-list-1440.png) · [375 px](catalog-label-kmeans-list-375.png) |
| Segment 1, after: empty, and says only a rule-based run fills a segment | [`catalog-label-kmeans-segment-1440.png`](catalog-label-kmeans-segment-1440.png) |

Read from the rendered list:

```
Demo Customer 1   customer1@mosaiq-demo.com   5500000001  Potential
Demo Customer 10  customer10@mosaiq-demo.com  5500000010  Champion
Demo Customer 11  customer11@mosaiq-demo.com  5500000011  Loyal
```

The search path (`?q=customer1@`) runs the other statement in `list_customers`
and gives the same label: `Demo Customer 1 … Potential`.

## After a rule-based run

A rule-based row names both a segment and its label, and the page shows both:

```
Current label Lost · Rule-based segment Segment 6 → /catalog/segments/6
```

| | |
|---|---|
| Customer detail: label and segment link | [`catalog-label-rules-customer-1440.png`](catalog-label-rules-customer-1440.png) |
| Segment 6: 17 members, captioned as rule-based assignments | [`catalog-label-rules-segment-375.png`](catalog-label-rules-segment-375.png) |

## 375 px and 1440 px

Horizontal overflow of the page body (`scrollWidth - clientWidth`) was **0 px** for
the customer detail, customer list and segment detail at 375 px. The list's wide
table scrolls inside its own panel, as it did before.

## Tests

`tests/test_catalog.py` and `tests/test_segments_db.py`:

* a K-means assignment shows its label, no segment link and not *Unassigned*;
* the unassigned result, and a customer never scored, show *Unassigned*;
* a rule-based assignment shows its label and still links its segment;
* the customer list shows each label or *Unassigned*, and both of its statements
  join `segment_label` on `h.label_code` and never read `h.segment_id`;
* the segment page says its members are rule-based assignments;
* `get_label_name` reads one label by code, as a parameter.

With `web/db/customers.py` and the templates reverted, six of these fail.

```
$ pytest -q
1241 passed
$ black --check .   # 103 files would be left unchanged
$ ruff check .      # All checks passed!
```

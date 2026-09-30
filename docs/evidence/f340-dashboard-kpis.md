# #340 — the dashboard's KPIs beyond the charts

The segmentation dashboard now also shows the figures the retrospective lists:
average R, F and M per label (with raw means), the experiments running today with
each arm's conversion rate, and the most recommended products with their stock.

## Where each number comes from, and how to check it

Replace `:run` with the run's id. Each should equal the page's figure.

```sql
-- Average R, F, M and the raw means, per label (the page's first table).
SELECT label_code, count(*), avg(r_score), avg(f_score), avg(m_score),
       avg(extract(epoch FROM ((SELECT run_at FROM segmentation_run WHERE run_id = :run)
                               - recency_last_purchase_at)) / 86400),
       avg(frequency_count), avg(monetary_total)
  FROM customer_segment_history
 WHERE run_id = :run AND label_code IS NOT NULL
 GROUP BY label_code;

-- Intent-to-treat conversion per arm of an experiment running today.
SELECT g.group_id, g.kind, count(a.assignment_id) AS assigned,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM experiment_conversion c
                                       WHERE c.assignment_id = a.assignment_id)) AS converted
  FROM experiment_group g
  LEFT JOIN experiment_assignment a ON a.group_id = g.group_id
 WHERE g.experiment_id = :experiment
 GROUP BY g.group_id, g.kind;
```

The recommended products are not a single query: each customer's list comes from
`recommend()` (RN-40), the same call the customer page makes, and the dashboard counts
the customers each product appears for. To check one, open a few customers'
recommendation pages and count.

## What was run

`tests/test_dashboard_kpis.py` (15 tests) checks the statement text and parameters,
the running-today rule and its boundaries, the ranking and tie-break, the per-store
stock, the cap notice and the page.

**On a real PostgreSQL 16** (Docker; clean load of the three scripts from this branch,
the application as `retail_app`, a fresh `RFM_RULES` run of the seeded customers, the page
driven in Chromium):

| Check | Result |
|---|---|
| Mean R, F, M, days since last purchase, purchases and spend, per label, against the query above | All six labels equal, to the digits shown |
| Running experiments and their arms (assigned, converted, rate) against the second query | 4 arms equal; the page lists the newest experiment first |
| Most recommended products: customers per product against each customer's own recommendation page (30 customers fetched) | All five products equal (22, 20, 20, 18, 9) |
| A `KMEANS` run | Mean R, F, M show dashes, raw means are filled, the caption says why |

The seed has no experiment running today, so two were moved to start five days ago with
no end date in the scratch database before checking their arms.

Captures: `f340-dashboard-1440.png`, `f340-dashboard-375.png`,
`f340-dashboard-kmeans-1440.png`.

## Found while checking

The stock column listed every store a product was recommended from: 22 stores for the top
product. It now shows the number of stores, the total units and the first five stores,
then "and N more".

## Still open

- The recommendation tally reads at most 100 customers of the run (each costs about ten
  queries). The run checked has 30 customers, so the cap and its notice were exercised
  only by a unit test, and the page time on a large run was not measured.

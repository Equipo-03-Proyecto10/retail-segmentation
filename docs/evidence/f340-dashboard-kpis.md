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

`tests/test_dashboard_kpis.py` (14 tests) checks the statement text and parameters,
the running-today rule and its boundaries, the ranking and tie-break, the per-store
stock, the cap notice and the page. The database is mocked.

## What was not run

- None of the SQL above was executed against PostgreSQL here, and the page was not
  compared with a live run, which is the last acceptance criterion. Someone needs to
  run the queries on a clean load and compare.
- The recommendation tally reads at most 100 customers of the run (each costs about
  ten queries). On a larger run the page says so; the first 100 by id are used.
- No 375 px or 1440 px captures were taken (`docs/process.md` §6).

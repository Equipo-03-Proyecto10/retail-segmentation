# F7-03 — The run history view

Evidence for F7-03 (#205), closed with no file here: every completed
segmentation run listed with its method, window, parameters, customer count and
executing user, and one run opened to see every customer it scored, with the
label, the raw recency, frequency and monetary values and the R, F and M scores.

No schema change, nothing here writes. The pages are server-rendered HTML; there
is no JSON endpoint.

## What was run, and against what

Branch `docs/443-analytics-evidence`, from `develop` at `f2b9c81`. The stack was
started with `docker compose -p ev443 -f compose.yaml -f compose.proxy.yaml up
--build -d` from an empty volume, so the three SQL scripts ran in order and
seeded runs 1 to 30 (PostgreSQL 16.14, NGINX 1.27.5 with the production
`Content-Security-Policy`, Chromium 153 driven by Playwright). The application
was reached at `http://127.0.0.1:8080`, through NGINX.

The seeded runs have no executing user. To have one, and a K-means run, and a run
with unassigned customers, four runs were made from the **Segment run** page as
the administrator after two customers were added in `psql` (customer 31 with one
sale imported through the CSV page, customer 32 with no sales): run 31
RFM_RULES over 180 days, run 32 K-means (k = 6, seed 17), run 33 RFM_RULES over
14 days, run 34 K-means (k = 6, seed 17) again. The other evidence files in this
phase describe the same database.

Screenshots are full-page captures at 375 px and 1440 px wide, taken signed in
as the demonstration administrator. The classic scrollbar was hidden for the
capture (`scrollbar-width: none`), so the layout is exactly 375 and 1440 CSS
pixels wide, as on a phone with overlay scrollbars. In every capture below
`scrollWidth` equalled `clientWidth` (no horizontal page scroll); wide tables
scroll inside their own wrapper, the pattern the rest of the application uses.

## The criteria

| Acceptance criterion | Proof |
|---|---|
| Completed runs: the list shows each run's execution time, method, scored window, parameters, customer count and executing user | The list below. Columns are Run, Method, Window, **K**, **Seed**, Customers, Executed by and Run at; the full parameter set is on each run's detail page |
| A selected run's detail lists its customers with label, raw R/F/M values and R/F/M scores | Run 33's detail below: label, the three scores, last purchase, orders and spend, checked against the stored rows |
| A run that produced unassigned customers shows them as unassigned, not omitted | Run 33, 17 of its 32 customers unassigned and listed as such |
| A profile without the permission is refused by the default-deny gate | `INVENTORY_PLANNER` and `CUSTOMER` get 403 on both pages, an anonymous request is redirected to sign in |

### The list

| Viewport | Capture |
|---|---|
| 1440 px | [Run history](f7-03-run-history-1440.png) |
| 375 px | [Run history](f7-03-run-history-375.png) |

The first rows read `#34 KMEANS 180 days 6 17 32 MOSAIQ Administrator
2026-10-19 01:41`, `#33 RFM_RULES 14 days — — 32 MOSAIQ Administrator
2026-10-19 01:39`, and down to `#30 RFM_RULES 180 days — — 30 System`, `System`
being a run with no executing user (the seed). The database says the same:

```text
$ psql -d retail -f f7_03.sql
== the run list as the database has it (newest five; K and seed are read out of the stored parameters)
 run |  method   | window | k | seed | customers |     executed_by      |      run_at      
-----+-----------+--------+---+------+-----------+----------------------+------------------
  34 | KMEANS    |    180 | 6 | 17   |        32 | MOSAIQ Administrator | 2026-10-19 01:41
  33 | RFM_RULES |     14 |   |      |        32 | MOSAIQ Administrator | 2026-10-19 01:39
  32 | KMEANS    |    180 | 6 | 17   |        32 | MOSAIQ Administrator | 2026-10-19 01:38
  31 | RFM_RULES |    180 |   |      |        32 | MOSAIQ Administrator | 2026-10-19 01:37
  30 | RFM_RULES |    180 |   |      |        30 | System               | 2026-10-19 01:32
(5 rows)
== run 33 detail rows for three customers, as stored (label, R/F/M scores, last purchase, orders, spend)
       name       |   label    | r | f | m | last_purchase | orders | spend  
------------------+------------+---+---+---+---------------+--------+--------
 Demo Customer 1  | CHAMPION   | 5 | 5 | 2 | 2026-10-18    |      4 | 311.75
 Demo Customer 10 | AT_RISK    | 2 | 3 | 5 | 2026-10-09    |      2 | 756.50
 Demo Customer 14 | Unassigned |   |   |   |               |        |       
(3 rows)
== run 33: customers scored and how many are unassigned
 customers_scored | unassigned 
------------------+------------
               32 |         17
(1 row)
```

### The detail

| What it shows | 1440 px | 375 px |
|---|---|---|
| Run 33, RFM_RULES over 14 days: label, R, F, M, last purchase, orders, spend; the 17 customers with no sale in the window listed as `Unassigned` with dashes | [capture](f7-03-run-detail-unassigned-1440.png) | [capture](f7-03-run-detail-unassigned-375.png) |
| Run 32, K-means: parameters, quality measures and the label mapping on the run; each customer's label with last purchase, orders and spend, and `—` for the three scores because a K-means run stores values but no quintile scores | [capture](f7-03-run-detail-kmeans-1440.png) | [capture](f7-03-run-detail-kmeans-375.png) |

Read against the stored rows above: *Demo Customer 1* is `CHAMPION 5 5 2`, last
purchase 2026-10-18, 4 orders, 311.75; *Demo Customer 10* is `AT_RISK 2 3 5`,
2026-10-09, 2 orders, 756.50; *Demo Customer 14* is `Unassigned`. The page says
`32 customers scored` and lists the 17 unassigned among them, 20 to a page.

### The refusal

| Viewport | Capture |
|---|---|
| 1440 px | [`INVENTORY_PLANNER` on the run list: 403](f7-03-refused-inventory-planner-1440.png) |
| 375 px | [`INVENTORY_PLANNER` on the run list: 403](f7-03-refused-inventory-planner-375.png) |

`INVENTORY_PLANNER` holds `report.read`, `catalog.read` and `inventory.write`,
not `segment.read`. The two routes against every profile, from the live matrix in
[F4-07 and F4-08](f4-07-f4-08-analytics-authorization.md):

```text
route                                       perm                      ADMIN  ANALYST STORE_MA MARKETIN INVENTOR  AUDITOR CUSTOMER ANONYMOU
GET /run-history/                           segment.read                200      200      200      200      403      200      403      302
GET /run-history/1                          segment.read                200      200      200      200      403      200      403      302
```

## Tests

```text
$ pytest tests/test_run_history.py
16 passed in 0.58s
```

`test_the_list_shows_method_window_customers_and_executor`,
`test_the_detail_shows_the_run_and_its_assignments`,
`test_unassigned_customers_are_shown_not_omitted`,
`test_a_profile_without_segment_read_is_refused[INVENTORY_PLANNER]` and
`[CUSTOMER]`, `test_signed_out_it_sends_you_to_sign_in`, and the K-means detail
tests (parameters, quality, label mapping).

## What was not run

- The `System` rows are the seed's runs; a run made by a real deployment's
  operator is the administrator's, shown by runs 31 to 34.
- Run 34's detail page was not captured; it is run 32's partition again
  ([F7-04 and F7-05](f7-04-f7-05-migration-and-matrix.md) shows the two agree for
  every customer).
- Run 46, a K-means run made afterwards for [#335](f335-kmeans-from-segment-run.md)'s
  result page, is not in these captures: the list and the SQL above are of 34 runs.
- Nothing was run on the Compute Engine instance.

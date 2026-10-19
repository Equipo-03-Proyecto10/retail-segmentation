# #335 — K-means from the Segment run page

Evidence for #335, closed with no file here, and for the two unreferenced captures
`an-18-segment-run-kmeans-1440.png` and `an-18-segment-run-kmeans-375.png`, which are
this page's K-means form. The administrator chooses between RFM rules and K-means on
the **Segment run** page; for K-means the form asks for K, a seed, an iteration limit
and a tolerance; the run records its method, parameters and quality measures with the
executing user; a bad K or parameter is a readable 400 and never a silent RFM_RULES
run; and run history shows K and seed as columns of their own.

No schema change was made and no application code was changed. The issue's own
reproduction, a post with `method=KMEANS&k=6&seed=17` that returned 200 and recorded an
`RFM_RULES` run, is the request the form now sends on **Confirm**; it is shown below
to record a `KMEANS` run.

## What was run, and against what

Branch `docs/443-analytics-evidence`, from `develop` at `f2b9c81`; the stack from
`docker compose -p ev443 -f compose.yaml -f compose.proxy.yaml up --build -d`
(PostgreSQL 16.14, NGINX 1.27.5 with the production CSP, Chromium 153 through
Playwright), as the demonstration administrator. Seeded runs 1 to 30 are RFM_RULES with
no executing user. K-means runs were made from the page: run 32 and run 34, each K = 6,
seed 17, 180 days, and, last of all and only to capture the result page, run 46 (no run
was stored with ids 35 to 45: identity values consumed by inserts that were rolled back,
as in [F7-01 and F7-02](f7-01-f7-02-segment-history-model.md), are not given back). The captures of the run list
were taken before run 46 and show runs up to 34.

Captures are full-page at 375 px and 1440 px with the classic scrollbar hidden, so the
layout is exactly 375 and 1440 CSS pixels wide; `scrollWidth` equalled `clientWidth` in
every one.

## The criteria

| Acceptance criterion | Proof |
|---|---|
| The administrator can choose RFM_RULES or KMEANS and, for KMEANS, K, seed, iteration limit and tolerance | The form below: choosing K-means reveals the four fields; K and seed are required, the iteration limit defaults to 100 and the tolerance to 0.0001 |
| A KMEANS submission records a `segmentation_run` with method KMEANS, its parameters and quality measures, and `executed_by` | Confirm and result pages below, and the stored run |
| Invalid K or parameters: a readable 400, never a silent RFM_RULES run | An invalid K through the page, and seven requests that bypass the form's own checks, with the run table unchanged |
| Run history lists a KMEANS run with K and seed as their own columns, not inside a raw parameter dump | The run history captures below |

### The form, the refusal, the confirmation, the result

| Step | 1440 px | 375 px |
|---|---|---|
| The form with K-means chosen and K = 6, seed = 17 entered (the K-means fields are hidden until the method is chosen: `hidden` was `true` before and `false` after) | [capture](an-18-segment-run-kmeans-1440.png) | [capture](an-18-segment-run-kmeans-375.png) |
| K = 1: the browser's own `min=2` check refuses it (`Value must be greater than or equal to 2.`), so the check was switched off to reach the server. Submit shows the confirmation (200); **Confirm** is refused with 400, `K-means was asked for k=1 clusters. It needs at least 2: a single cluster has no best-to-worst order to pair with the labels.` | [capture](f335-invalid-k-1440.png) | [capture](f335-invalid-k-375.png) |
| K = 6, seed = 17: the confirmation, `This runs K-means (K=6, seed=17) over the last 180 days of sales.` | [capture](f335-confirm-kmeans-1440.png) | [capture](f335-confirm-kmeans-375.png) |
| After **Confirm**: the result of run 46, `KMEANS`, 32 customers processed, 31 assigned, 1 with no sales left unassigned | [capture](f335-result-kmeans-1440.png) | [capture](f335-result-kmeans-375.png) |

Two observations. The server-side check of K runs when the run is confirmed, not when
the form is first submitted, so a K of 1 reaches the confirmation page (200) and is
refused after it; the refusal is still a readable 400 and no run is written. And the
result page of run 46 reads `No segment changed`, because it repeats run 34's fit:
every customer kept their label (the SQL below), and the run was still recorded with one
row per customer.

### What a run records

From the database, run 34, made from the page earlier in the same way:

```text
$ psql -d retail -f f335_run.sql
== run 34, made from the Segment run page: method, window, who ran it, and what it recorded
 run_id | method | window_days |      executed_by      | customer_count | k | seed | max_iterations | tolerance 
--------+--------+-------------+-----------------------+----------------+---+------+----------------+-----------
     34 | KMEANS |         180 | admin@mosaiq-demo.com |             32 | 6 | 17   | 100            | 0.0001
(1 row)
== its quality measures and label mapping, as stored
            quality             |             mapping_rule             |                            labels_by_rank                            
--------------------------------+--------------------------------------+----------------------------------------------------------------------
 {                             +| rank_proportional_endpoints_anchored | ["CHAMPION", "LOYAL", "POTENTIAL", "AT_RISK", "HIBERNATING", "LOST"]
     "inertia": 0.632948,      +|                                      | 
     "converged": true,        +|                                      | 
     "iterations": 5,          +|                                      | 
     "silhouette": 0.408068,   +|                                      | 
     "stopped_on": "tolerance",+|                                      | 
     "final_shift": 0.0,       +|                                      | 
     "cluster_sizes": [        +|                                      | 
         8,                    +|                                      | 
         7,                    +|                                      | 
         6,                    +|                                      | 
         5,                    +|                                      | 
         4,                    +|                                      | 
         1                     +|                                      | 
     ],                        +|                                      | 
     "customers_clustered": 31,+|                                      | 
     "customers_unassigned": 1,+|                                      | 
     "empty_cluster_events": 0 +|                                      | 
 }                              |                                      | 
(1 row)
== how many runs of each kind and who ran them
  method   |      executed_by      | runs 
-----------+-----------------------+------
 KMEANS    | admin@mosaiq-demo.com |    2
 RFM_RULES | admin@mosaiq-demo.com |    2
 RFM_RULES | System (seed)         |   30
(3 rows)
```

Method, window, `executed_by`, K, seed, iteration limit and tolerance are on the run, and
so are its quality measures (inertia, silhouette, iterations, whether it converged, the
cluster sizes) and the mapping of clusters to labels. Run 46, made last:

```text
$ psql -d retail -f f335_after.sql
== run 46, made last from the Segment run page (K = 6, seed = 17, 180 days): the same fit as run 34
 run_id | method | window_days |      executed_by      | k | seed | customer_count |            run_at             
--------+--------+-------------+-----------------------+---+------+----------------+-------------------------------
     46 | KMEANS |         180 | admin@mosaiq-demo.com | 6 | 17   |             32 | 2026-10-19 14:20:11.173321+00
(1 row)
== it moved nobody: runs 34 and 46 give every customer the same label
 customers | same_label 
-----------+------------
        32 |         32
(1 row)
== runs of each kind, and who ran them (now including run 46)
  method   |      executed_by      | runs 
-----------+-----------------------+------
 KMEANS    | admin@mosaiq-demo.com |    3
 RFM_RULES | admin@mosaiq-demo.com |    2
 RFM_RULES | System (seed)         |   30
(3 rows)
```

### Refused, not silent

Seven requests were sent as the administrator to `POST /segment-run/` with the
confirmation field already present, so that nothing but the server's own checks stood
in the way; the run table was read before and after:

```text
$ python f335_forged.py
before -> runs: 34, last run 34 KMEANS
method=KMEANS, no k, no seed                 -> 400  K must be a whole number.
method=KMEANS, k=abc                         -> 400  K must be a whole number.
method=KMEANS, k=1                           -> 400  K-means was asked for k=1 clusters. It needs at least 2: a single cluster has no best-to-worst order to pair with the labels.
method=KMEANS, k=6, seed missing             -> 400  Seed must be a whole number.
method=KMEANS, k=6, seed=17, tolerance=x     -> 400  Tolerance must be a number.
method=BOGUS                                 -> 400  Choose RFM rules or K-means.
method=RFM_RULES, window=0                   -> 400  The window must be between 1 and 3650 days.
after  -> runs: 34, last run 34 KMEANS
```

Each is a 400 with a sentence that names the field, and the number of runs and the last
run are the same before and after: nothing was recorded, least of all an `RFM_RULES`
run.

### The run history

K and seed are columns of the list, and the run's detail page prints its parameters as
`key: value` pairs and its quality measures on a line of their own, not as a Python
dictionary:

| What | 1440 px | 375 px |
|---|---|---|
| The run list: `#34 KMEANS 180 days 6 17 …`, `#33 RFM_RULES 14 days — —`; K and seed are `—` for a rule-based run | [capture](f7-03-run-history-1440.png) | [capture](f7-03-run-history-375.png) |
| Run 32's detail: `k: 6, seed: 17, …`, then `Quality` and `Label mapping` | [capture](f7-03-run-detail-kmeans-1440.png) | [capture](f7-03-run-detail-kmeans-375.png) |

Those captures belong to [F7-03](f7-03-run-history.md), where they are described.

## Tests

```text
$ pytest tests/test_segment_run_kmeans.py tests/test_kmeans_run.py
34 passed in 0.58s
```

## What was not run

- A seed, iteration limit or tolerance that the browser would refuse (a non-numeric
  tolerance, say) was sent only through the script above, not typed into the page.
- The 375 px capture of the K = 1 refusal and of the other server-rendered pages shows
  the page as rendered after a resize from 1440 px; the pages were not re-submitted at
  375 px.
- Nothing was run on the Compute Engine instance.

## The scripts

<details><summary><code>f335_forged.py</code> (needs the sign-in helper <code>mq.py</code>, listed in <a href="f7-04-f7-05-migration-and-matrix.md">F7-04 and F7-05</a>)</summary>

```python
"""#335: requests that bypass the form's own checks, and what the run table says before and after."""
import re, subprocess
from mq import Client

def runs():
    out = subprocess.run(["docker", "exec", "ev443-db-1", "psql", "-U", "postgres", "-d", "retail", "-At", "-c",
                          "select 'runs: ' || count(*) || ', last run ' || max(run_id) || ' ' || (select method from segmentation_run order by run_id desc limit 1) from segmentation_run"],
                         capture_output=True, text=True, check=True).stdout.strip()
    return out

print("before ->", runs())
c = Client(); c.login("admin@mosaiq-demo.com")
cases = [
    ("method=KMEANS, no k, no seed", {"method": "KMEANS", "window": "180", "confirm": "yes"}),
    ("method=KMEANS, k=abc", {"method": "KMEANS", "window": "180", "k": "abc", "seed": "17", "confirm": "yes"}),
    ("method=KMEANS, k=1", {"method": "KMEANS", "window": "180", "k": "1", "seed": "17", "confirm": "yes"}),
    ("method=KMEANS, k=6, seed missing", {"method": "KMEANS", "window": "180", "k": "6", "confirm": "yes"}),
    ("method=KMEANS, k=6, seed=17, tolerance=x", {"method": "KMEANS", "window": "180", "k": "6", "seed": "17", "tolerance": "x", "confirm": "yes"}),
    ("method=BOGUS", {"method": "BOGUS", "window": "180", "confirm": "yes"}),
    ("method=RFM_RULES, window=0", {"method": "RFM_RULES", "window": "0", "confirm": "yes"}),
]
for label, data in cases:
    s, h, t = c.post("/segment-run/", data, token_from="/segment-run/")
    txt = re.findall(r'mq-alert__text">([^<]+)', t)
    print(f"{label:44} -> {s}  {txt[-1] if txt else ''}")
print("after  ->", runs())
```

</details>

<details><summary><code>f335_run.sql</code> and <code>f335_after.sql</code></summary>

```sql
\pset pager off
\echo '== run 34, made from the Segment run page: method, window, who ran it, and what it recorded'
SELECT r.run_id, r.method, r.window_days, u.email AS executed_by, r.customer_count,
       r.parameters->>'k' AS k, r.parameters->>'seed' AS seed, r.parameters->>'max_iterations' AS max_iterations, r.parameters->>'tolerance' AS tolerance
  FROM segmentation_run r JOIN app_user u ON u.user_id = r.executed_by WHERE r.run_id = 34;
\echo '== its quality measures and label mapping, as stored'
SELECT jsonb_pretty(parameters->'quality') AS quality, parameters->'label_mapping'->>'rule' AS mapping_rule,
       parameters->'label_mapping'->'labels_by_rank' AS labels_by_rank
  FROM segmentation_run WHERE run_id = 34;
\echo '== how many runs of each kind and who ran them'
SELECT r.method, coalesce(u.email, 'System (seed)') AS executed_by, count(*) AS runs FROM segmentation_run r LEFT JOIN app_user u ON u.user_id = r.executed_by GROUP BY 1, 2 ORDER BY 1, 2;
```

```sql
\pset pager off
\echo '== run 46, made last from the Segment run page (K = 6, seed = 17, 180 days): the same fit as run 34'
SELECT r.run_id, r.method, r.window_days, u.email AS executed_by, r.parameters->>'k' AS k, r.parameters->>'seed' AS seed, r.customer_count, r.run_at
  FROM segmentation_run r JOIN app_user u ON u.user_id = r.executed_by WHERE r.run_id = 46;
\echo '== it moved nobody: runs 34 and 46 give every customer the same label'
SELECT count(*) AS customers, count(*) FILTER (WHERE a.label_code IS NOT DISTINCT FROM b.label_code) AS same_label
  FROM customer_segment_history a JOIN customer_segment_history b USING (customer_id) WHERE a.run_id = 34 AND b.run_id = 46;
\echo '== runs of each kind, and who ran them (now including run 46)'
SELECT r.method, coalesce(u.email, 'System (seed)') AS executed_by, count(*) AS runs FROM segmentation_run r LEFT JOIN app_user u ON u.user_id = r.executed_by GROUP BY 1, 2 ORDER BY 1, 2;
```

</details>

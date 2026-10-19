# F7-04 and F7-05 — Label migration between two runs, and the migration matrix

Evidence for F7-04 (#206) and F7-05 (#207), closed with no file here. F7-04
classifies every customer between any two completed runs as unchanged, moved
(with a direction), newly assigned or newly unassigned, comparing stable label
codes only. F7-05 shows the same comparison as a matrix of counts: rows are the
earlier run's labels, columns the later run's.

No schema change, nothing here writes. The two stale, unreferenced captures
`f7-05-migration-matrix-1440.png` and `f7-05-migration-matrix-375.png` were
retaken, because they predated the current navigation and showed a 360 px layout;
they are referenced below.

## What was run, and against what

Branch `docs/443-analytics-evidence`, from `develop` at `f2b9c81`; the stack from
`docker compose -p ev443 -f compose.yaml -f compose.proxy.yaml up --build -d`
(PostgreSQL 16.14, NGINX 1.27.5 with the production CSP, Chromium 153 through
Playwright). Seeded runs 1 to 30, plus four runs made from the **Segment run**
page after two customers were added in `psql` (customer 31 with one imported
sale, customer 32 with no sales): 31 RFM_RULES over 180 days, 32 K-means (k = 6,
seed 17), 33 RFM_RULES over 14 days, 34 K-means (k = 6, seed 17) again. The pairs
compared below were chosen to reach every category the data allows:

| Pair | What it contains |
|---|---|
| 30 → 31 | Moves in both directions; two customers absent from run 30 (31 *Evidence Newcomer* scored, 32 *Evidence No Sales* unassigned) |
| 31 → 32 | The two **methods** compared: RFM_RULES against K-means |
| 31 → 33 | Moves, and 16 customers who had a label and then no sale in the shorter window: newly unassigned |
| 33 → 34 | The reverse, 16 customers newly assigned |
| 32 → 34 | The same K-means fit run twice: no customer should move |

Screenshots are full-page captures at 375 px and 1440 px wide as the
administrator, with the classic scrollbar hidden so the layout is exactly 375 and
1440 CSS pixels wide (`scrollWidth` equalled `clientWidth` in every one). Wide
tables scroll inside their own wrapper.

## F7-04 — the migration (#206)

| Acceptance criterion | Proof |
|---|---|
| Two completed runs: each customer is unchanged, moved with a direction, newly assigned, or newly unassigned | `compute_migration` over the five pairs, and one of them again in reverse order, against an independent SQL classification, below; the categories shown on the page by the drill-down captures |
| Two runs from different methods: migration succeeds and compares label codes only | Pair 31 → 32 (RFM_RULES against K-means) succeeds and agrees with SQL; `list_run_labels` reads `customer_id, label_code` and nothing else |
| A run whose raw cluster ids are permuted with the partition unchanged migrates identically | Not shown on real rows, see below. Shown by: the read that cannot see a cluster id, pair 32 → 34, and the tests |
| A customer absent from one run is reported in its own category, not dropped | Pair 30 → 31: two customers in the *Not in earlier run* row; the `NEW` drill-down |

The application's own `compute_migration` was called against the database as
`retail_app`, for each pair, and every category count was compared with a
classification written separately in SQL (a full outer join of the two runs'
history rows, the label order read from `segment_label.ordinal_position`):

```text
$ python f7_04_check.py
runs 30, 31: absent_from_earlier=2, moved/declined=8, moved/improved=6, unchanged=16   SQL agrees
runs 31, 32: moved/declined=8, moved/improved=14, unchanged=10   SQL agrees
runs 31, 33: moved/declined=7, newly_unassigned=16, unchanged=9   SQL agrees
runs 33, 34: moved/declined=4, moved/improved=8, newly_assigned=16, unchanged=4   SQL agrees
runs 32, 34: unchanged=32   SQL agrees
runs 34, 31: moved/declined=8, moved/improved=14, unchanged=10   SQL agrees
all pairs agree: True
```

`moved/improved` is a move to a label with a lower `ordinal_position`
(ADR-0018's best-to-worst order), `moved/declined` the reverse. The last line
passes the pair in reverse order and gets the same result as 31 → 32, because
runs are ordered by their own `run_at`.

The page shows the categories through the matrix drill-down: selecting a cell
lists its customers with *Before*, *After* and *Direction*.

| Category | 1440 px | 375 px | What it shows |
|---|---|---|---|
| Improved | [capture](f7-04-cell-improved-1440.png) | [capture](f7-04-cell-improved-375.png) | 31 → 32, `LOYAL → CHAMPION`: seven customers, *Direction* `improved` |
| Declined | [capture](f7-04-cell-declined-1440.png) | [capture](f7-04-cell-declined-375.png) | 31 → 32, `CHAMPION → LOYAL`: three customers, `declined` |
| Newly unassigned | [capture](f7-04-cell-newly-unassigned-1440.png) | [capture](f7-04-cell-newly-unassigned-375.png) | 31 → 33, `LOYAL → Unassigned`: customers 14 to 17, no direction (`—`) |
| Absent from the earlier run | [capture](f7-04-cell-new-customers-1440.png) | [capture](f7-04-cell-new-customers-375.png) | 30 → 31, customers from `NEW`: *Evidence Newcomer* to `POTENTIAL`, *Evidence No Sales* to `Unassigned`; neither is shown as moved |

Newly assigned (33 → 34, 16 customers) is in the SQL comparison above but was not
captured as a drill-down.

**Labels only.** The read behind both runs is:

```text
$ grep -n -A12 "def list_run_labels" web/db/segments.py
522:def list_run_labels(connection: Connection[Any], run_id: int) -> dict[Any, str | None]:
523-    """Return every customer this run scored, mapped to the label_code it
524-    assigned — None where the customer was scored but left unassigned
525-    (RN-21). SQL only: the migration classification itself lives in
526-    web/services/segment_migration.py (ADR-0003)."""
527-    with connection.cursor() as cursor:
528-        cursor.execute(
529-            "SELECT customer_id, label_code FROM customer_segment_history"
530-            " WHERE run_id = %s",
531-            (run_id,),
532-        )
533-        return dict(cursor.fetchall())
534-
```

It names no cluster id, no `segment_id` and no method. `tests/test_segment_migration.py`
holds `test_signature_never_admits_method_or_segment_id` and
`test_run_queries_read_label_code_never_segment_id_or_method`, which fail if that
changes.

**Permuted cluster ids.** History stores the label a cluster was paired with and
not the cluster's number, so a permutation cannot reach the migration. What was
observed on real rows: pair 32 → 34, the same K-means fit run twice, migrates
nobody (`unchanged=32`), so a second fit does not report a spurious 100% movement.
What was **not** observed on real rows is a second fit that numbers the same
partition differently: five other seeds (3, 5, 11, 99, 123) were tried on a copy
of the database and each produced a different partition, not a renumbering of
this one. The renumbering is covered by tests on constructed fits:
`tests/test_cluster_labels.py::test_renaming_every_cluster_of_a_fixed_partition_changes_no_customers_label`
(every permutation of five clusters gives every customer the same label).

**Absent from the later run.** `absent_from_later` has no real example here, since
no run in this data leaves out a customer a prior run scored. It is covered by
`test_a_customer_only_in_the_earlier_run_is_its_own_category` and
`test_nobody_is_silently_dropped`.

## F7-05 — the migration matrix (#207)

| Acceptance criterion | Proof |
|---|---|
| Rows are the earlier run's labels, columns the later run's, cells customer counts | Matrix 31 → 33 below |
| Row and column totals reconcile with the per-run assignment counts | `f7_05_reconcile.py` below: every row and column total of three matrices against `count(*)` per label in each run |
| Customers unassigned in either run have their own row and column | `Unassigned` row and column in every capture; in 31 → 33 the column holds 17 |
| Readable at 375 px and 1440 px | Captures below, and the table scrolled to its last columns at 375 px |

| Matrix | 1440 px | 375 px |
|---|---|---|
| 31 → 33: moves, and 16 newly unassigned in the `Unassigned` column | [capture](f7-05-migration-matrix-1440.png) | [capture](f7-05-migration-matrix-375.png) |
| 30 → 31: the *Not in earlier run* row holds two customers | [capture](f7-05-migration-matrix-new-customers-1440.png) | [capture](f7-05-migration-matrix-new-customers-375.png) |
| 31 → 32: RFM_RULES against K-means | [capture](f7-05-migration-matrix-rfm-to-kmeans-1440.png) | [capture](f7-05-migration-matrix-rfm-to-kmeans-375.png) |

At 375 px the matrix is a wider table inside its own scroll container: the first
column, the run pickers and the Compare button stay in view, and the label columns
scroll sideways. [The same matrix scrolled to its last columns](f7-05-migration-matrix-scrolled-375.png)
shows `Unassigned`, `Not in later run` and `Row total`. At 1440 px every column
fits.

```text
$ python f7_05_reconcile.py        (signs in as the administrator, fetches each matrix, reads the table)
run #31 (rows) against run #33 (columns), HTTP 200
   row    CHAMPION             matrix   3   run #31 has   3   ok
   row    LOYAL                matrix  15   run #31 has  15   ok
   row    POTENTIAL            matrix   1   run #31 has   1   ok
   row    AT_RISK              matrix   6   run #31 has   6   ok
   row    HIBERNATING          matrix   6   run #31 has   6   ok
   row    LOST                 matrix   0   run #31 has   0   ok
   row    Unassigned           matrix   1   run #31 has   1   ok
   row    Not in earlier run   matrix   0   run #31 has   0   ok
   column CHAMPION             matrix   3   run #33 has   3   ok
   column LOYAL                matrix   4   run #33 has   4   ok
   column POTENTIAL            matrix   2   run #33 has   2   ok
   column AT_RISK              matrix   3   run #33 has   3   ok
   column HIBERNATING          matrix   3   run #33 has   3   ok
   column LOST                 matrix   0   run #33 has   0   ok
   column Unassigned           matrix  17   run #33 has  17   ok
   column Not in later run     matrix   0   run #33 has   0   ok
   grand total 32 = 32 = customers in the later run 32
run #30 (rows) against run #31 (columns), HTTP 200
   row    CHAMPION             matrix  11   run #30 has  11   ok
   row    LOYAL                matrix   6   run #30 has   6   ok
   row    POTENTIAL            matrix   1   run #30 has   1   ok
   row    AT_RISK              matrix   1   run #30 has   1   ok
   row    HIBERNATING          matrix   9   run #30 has   9   ok
   row    LOST                 matrix   2   run #30 has   2   ok
   row    Unassigned           matrix   0   run #30 has   0   ok
   row    Not in earlier run   matrix   2   run #30 has   2   ok
   column CHAMPION             matrix   3   run #31 has   3   ok
   column LOYAL                matrix  15   run #31 has  15   ok
   column POTENTIAL            matrix   1   run #31 has   1   ok
   column AT_RISK              matrix   6   run #31 has   6   ok
   column HIBERNATING          matrix   6   run #31 has   6   ok
   column LOST                 matrix   0   run #31 has   0   ok
   column Unassigned           matrix   1   run #31 has   1   ok
   column Not in later run     matrix   0   run #31 has   0   ok
   grand total 32 = 32 = customers in the later run 32
run #31 (rows) against run #32 (columns), HTTP 200
   row    CHAMPION             matrix   3   run #31 has   3   ok
   row    LOYAL                matrix  15   run #31 has  15   ok
   row    POTENTIAL            matrix   1   run #31 has   1   ok
   row    AT_RISK              matrix   6   run #31 has   6   ok
   row    HIBERNATING          matrix   6   run #31 has   6   ok
   row    LOST                 matrix   0   run #31 has   0   ok
   row    Unassigned           matrix   1   run #31 has   1   ok
   row    Not in earlier run   matrix   0   run #31 has   0   ok
   column CHAMPION             matrix   7   run #32 has   7   ok
   column LOYAL                matrix   8   run #32 has   8   ok
   column POTENTIAL            matrix   6   run #32 has   6   ok
   column AT_RISK              matrix   5   run #32 has   5   ok
   column HIBERNATING          matrix   4   run #32 has   4   ok
   column LOST                 matrix   1   run #32 has   1   ok
   column Unassigned           matrix   1   run #32 has   1   ok
   column Not in later run     matrix   0   run #32 has   0   ok
   grand total 32 = 32 = customers in the later run 32
every row and column total reconciles with the runs: True
```

## Tests

```text
$ pytest tests/test_segment_migration.py tests/test_migration_matrix.py tests/test_migration_matrix_route.py
47 passed in 0.61s
```

## What was not run

- No second K-means fit that renumbers the same partition, and no
  `absent_from_later` customer, on real rows (see above); both are covered by
  tests on constructed inputs only.
- Pair 33 → 34 (newly assigned) was checked against SQL but not captured.
- Nothing was run on the Compute Engine instance.

## The scripts

<details><summary><code>f7_04_check.py</code></summary>

```python
"""F7-04: the migration the application computes, against an independent SQL classification."""
import sys
from collections import Counter
sys.path.insert(0, "<repo>")
import psycopg
from web.services.segment_migration import compute_migration

conn = psycopg.connect("postgresql://retail_app:retail_app@127.0.0.1:5432/retail")

SQL = """
SELECT CASE
         WHEN a.customer_id IS NULL THEN 'absent_from_earlier'
         WHEN b.customer_id IS NULL THEN 'absent_from_later'
         WHEN a.label_code IS NULL AND b.label_code IS NULL THEN 'unchanged'
         WHEN a.label_code IS NULL THEN 'newly_assigned'
         WHEN b.label_code IS NULL THEN 'newly_unassigned'
         WHEN a.label_code = b.label_code THEN 'unchanged'
         WHEN la.ordinal_position > lb.ordinal_position THEN 'moved/improved'
         ELSE 'moved/declined'
       END AS category, count(*)
  FROM (SELECT * FROM customer_segment_history WHERE run_id = %s) a
  FULL JOIN (SELECT * FROM customer_segment_history WHERE run_id = %s) b ON b.customer_id = a.customer_id
  LEFT JOIN segment_label la ON la.label_code = a.label_code
  LEFT JOIN segment_label lb ON lb.label_code = b.label_code
 GROUP BY 1 ORDER BY 1
"""

def app_counts(earlier, later):
    c = Counter()
    for m in compute_migration(conn, earlier, later):
        c[m.category.value + (("/" + m.direction.value) if m.direction else "")] += 1
    return c

def sql_counts(earlier, later):
    with conn.cursor() as cur:
        cur.execute(SQL, (earlier, later))
        return Counter({k: n for k, n in cur.fetchall()})

ok = True
for earlier, later in [(30, 31), (31, 32), (31, 33), (33, 34), (32, 34), (34, 31)]:
    a, s = app_counts(earlier, later), sql_counts(min(earlier, later), max(earlier, later))
    same = a == s
    ok &= same
    print(f"runs {earlier}, {later}: " + ", ".join(f"{k}={v}" for k, v in sorted(a.items())) + ("   SQL agrees" if same else f"   MISMATCH sql={dict(s)}"))
conn.rollback()
print("all pairs agree:", ok)
```

</details>

<details><summary><code>f7_05_reconcile.py</code> (needs the small sign-in helper <code>mq.py</code>)</summary>

```python
"""F7-05: the migration matrix page against each run's own assignment counts, read straight from the table."""
import re, subprocess
from html.parser import HTMLParser
from mq import Client

def sql(q):
    out = subprocess.run(["docker", "exec", "ev443-db-1", "psql", "-U", "postgres", "-d", "retail", "-At", "-F", "|", "-c", q],
                         capture_output=True, text=True, check=True).stdout
    return [l.split("|") for l in out.strip().splitlines()]

class Table(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows = []; self.cell = None; self.row = None; self.depth = 0
    def handle_starttag(self, tag, attrs):
        if tag == "table": self.depth += 1
        if self.depth == 1 and tag == "tr": self.row = []
        if self.depth == 1 and tag in ("td", "th"): self.cell = ""
    def handle_endtag(self, tag):
        if tag == "table": self.depth -= 1
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join(self.cell.split())); self.cell = None
        if tag == "tr" and self.row is not None and self.depth == 1:
            self.rows.append(self.row); self.row = None
    def handle_data(self, d):
        if self.cell is not None: self.cell += d

c = Client(); c.login("admin@mosaiq-demo.com")
ok = True
for earlier, later in [(31, 33), (30, 31), (31, 32)]:
    s, _, html = c.get(f"/migration-matrix/?run_a={earlier}&run_b={later}")
    t = Table(); t.feed(html[html.index('<table'):])
    header, body = t.rows[0], t.rows[1:]
    cols = header[1:-1]                                   # label columns, then Row total
    row_total = {r[0]: int(r[-1]) for r in body if r[0] != "Column total"}
    col_total = dict(zip(cols, map(int, body[-1][1:-1])))
    def counts(run):
        return {(l or "Unassigned"): int(n) for l, n in sql(f"SELECT coalesce(label_code, ''), count(*) FROM customer_segment_history WHERE run_id = {run} GROUP BY 1")}
    e, l = counts(earlier), counts(later)
    absent_earlier = int(sql(f"SELECT count(*) FROM customer_segment_history b WHERE b.run_id = {later} AND NOT EXISTS (SELECT 1 FROM customer_segment_history a WHERE a.run_id = {earlier} AND a.customer_id = b.customer_id)")[0][0])
    print(f"run #{earlier} (rows) against run #{later} (columns), HTTP {s}")
    for label in [r[0] for r in body[:-1]]:
        want_row = e.get(label, 0) if label != "Not in earlier run" else absent_earlier
        got = row_total[label]
        mark = "ok" if got == want_row else "MISMATCH"; ok &= got == want_row
        print(f"   row    {label:20} matrix {got:3}   run #{earlier} has {want_row:3}   {mark}")
    for label in cols:
        want_col = l.get(label, 0) if label != "Not in later run" else 0
        got = col_total[label]
        mark = "ok" if got == want_col else "MISMATCH"; ok &= got == want_col
        print(f"   column {label:20} matrix {got:3}   run #{later} has {want_col:3}   {mark}")
    print(f"   grand total {sum(row_total.values())} = {sum(col_total.values())} = customers in the later run {sum(l.values())}")
print("every row and column total reconciles with the runs:", ok)
```

```python
# mq.py
"""Tiny HTTP client for the evidence runs: sign in as a demo account, GET and POST with CSRF."""
import http.cookiejar, re, time, urllib.parse, urllib.request, urllib.error

BASE = "http://127.0.0.1:8080"
PASSWORD = "Password123!"
_LAST_LOGIN = [0.0]
# NGINX throttles POST /login to 6 a minute (deploy/nginx/mosaiq.compose.conf), so sign-ins are paced.
_PACE = 10.5


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect()
        )

    def request(self, method, path, data=None, headers=None):
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
        try:
            r = self.opener.open(req, timeout=60)
        except urllib.error.HTTPError as e:
            r = e
        text = r.read().decode("utf-8", "replace")
        return r.status if hasattr(r, "status") else r.code, dict(r.headers), text

    def get(self, path):
        return self.request("GET", path)

    def token(self, path="/login"):
        status, _, html = self.get(path)
        m = re.search(r'name="csrf_token" value="([^"]+)"', html)
        return m.group(1) if m else None

    def login(self, email):
        wait = _LAST_LOGIN[0] + _PACE - time.time()
        if wait > 0:
            time.sleep(wait)
        _LAST_LOGIN[0] = time.time()
        tok = self.token("/login")
        status, hdr, _ = self.request("POST", "/login", {"csrf_token": tok, "email": email, "password": PASSWORD})
        return status, hdr.get("Location")

    def post(self, path, data, token_from=None):
        tok = self.token(token_from or path)
        d = dict(data)
        d["csrf_token"] = tok
        return self.request("POST", path, d)
```

</details>

# #344 — The analytics reports index

Evidence for #344, closed with no file here. `/reports/` rendered a placeholder
(`Analytical reports and dashboards are still being built.`) for every role that holds
`report.read`, and for `INVENTORY_PLANNER`, which holds `report.read` and not
`segment.read`, it was the only analytics link in the menu. It is now an index: a
revenue summary for anyone holding `report.read`, and links to the analytics reports
the signed-in profile may open.

No schema change was made and no application code was changed (the fix is the commit
`e418792`).

## What was run, and against what

Branch `docs/443-analytics-evidence`, from `develop` at `f2b9c81`; the stack from
`docker compose -p ev443 -f compose.yaml -f compose.proxy.yaml up --build -d`
(PostgreSQL 16.14, NGINX 1.27.5 with the production CSP, Chromium 153 through
Playwright), with the seeded sales plus six transactions added through the CSV import (see
[F8-01 and F8-02](f8-01-f8-02-sales-import.md)). Each profile was signed in through the
real form. Captures are full-page at 375 px and 1440 px with the classic scrollbar
hidden, so the layout is exactly 375 and 1440 CSS pixels wide; `scrollWidth` equalled
`clientWidth` in every one.

## The criteria

The issue states a reproduction rather than a list of criteria: *sign in as any role
holding `report.read`, open `/reports/`*, and expects analytical reports, with the
revenue dashboard gated on `report.read` as `docs/analytics-permission-map.md` says.
The checks below take it apart:

| Expectation | Proof |
|---|---|
| The placeholder sentence is gone | The grep below, and `/reports/` as every profile |
| Every profile with `report.read` gets a page, with the revenue summary | The per-profile table below, and the captures |
| No profile is offered a link that answers 403 (the reported fault for `INVENTORY_PLANNER`) | Each offered link was requested as the same profile: all 200; `INVENTORY_PLANNER` is offered none |
| The revenue figures are right | The page against a query that recomputes them from the transaction lines |
| A profile without `report.read` is refused | `CUSTOMER` gets 403 |

```text
$ grep -rn "still being built\|are deferred in docs/roadmap" web/
(no output; exit status 1)

$ git show e418792 --stat | head -12   # the commit that replaced the placeholder
e418792 fix(reports): replace the reports placeholder with the analytics reports index and revenue summary (#344)

 tests/test_authz.py                   |  10 ---
 tests/test_reports_index.py           | 160 ++++++++++++++++++++++++++++++++++
 web/db/revenue_summary.py             |  74 ++++++++++++++++
 web/routes/__init__.py                |   2 +-
 web/routes/placeholders.py            |  27 ------
 web/routes/reports.py                 |  96 ++++++++++++++++++++
 web/templates/reports/index.html      |  59 +++++++++++++
 web/templates/status/coming_soon.html |  19 ----
 8 files changed, 390 insertions(+), 57 deletions(-)
```

### Each profile

Each profile signed in through the form and requested `/reports/`, then every link the
page offered:

```text
$ python f344_reports.py
ADMIN              GET /reports/ -> 200  revenue=42,163.00  placeholder-sentence=False  links=['Segmentation dashboard', 'Segment history report', 'Consumption reports', 'Experiment report']
                     links that do not answer 200 for this profile: []
ANALYST            GET /reports/ -> 200  revenue=42,163.00  placeholder-sentence=False  links=['Segmentation dashboard', 'Segment history report', 'Consumption reports', 'Experiment report']
                     links that do not answer 200 for this profile: []
STORE_MANAGER      GET /reports/ -> 200  revenue=42,163.00  placeholder-sentence=False  links=['Segmentation dashboard', 'Segment history report', 'Consumption reports']
                     links that do not answer 200 for this profile: []
MARKETING          GET /reports/ -> 200  revenue=42,163.00  placeholder-sentence=False  links=['Segmentation dashboard', 'Segment history report', 'Consumption reports', 'Experiment report']
                     links that do not answer 200 for this profile: []
INVENTORY_PLANNER  GET /reports/ -> 200  revenue=42,163.00  placeholder-sentence=False  links=[]
                     links that do not answer 200 for this profile: []
AUDITOR            GET /reports/ -> 200  revenue=42,163.00  placeholder-sentence=False  links=['Segmentation dashboard', 'Segment history report', 'Consumption reports', 'Experiment report']
                     links that do not answer 200 for this profile: []
CUSTOMER           GET /reports/ -> 403
```

`INVENTORY_PLANNER` sees the revenue summary and no links: the segmentation reports
need `segment.read` and the experiment report `campaign.read`, which it does not hold, so
the index offers nothing it would refuse. `STORE_MANAGER` is offered the three
segmentation reports and not the experiment report (no `campaign.read`). `ANALYST`,
`MARKETING` and `AUDITOR` are offered all four, and `ADMIN` too.

| Profile | 1440 px | 375 px |
|---|---|---|
| `INVENTORY_PLANNER`: the revenue summary and no report links, the case the issue reported | [capture](f344-reports-inventory-planner-1440.png) | [capture](f344-reports-inventory-planner-375.png) |
| `ANALYST`: the revenue summary and four report links | [capture](f344-reports-analyst-1440.png) | [capture](f344-reports-analyst-375.png) |
| `STORE_MANAGER`: the revenue summary and three report links | [capture](f344-reports-store-manager-1440.png) | [capture](f344-reports-store-manager-375.png) |
| `CUSTOMER`: 403 | [capture](f344-reports-customer-refused-1440.png) | [capture](f344-reports-customer-refused-375.png) |

The page carries the `Synthetic` pill and the line `These figures come from
demonstration data, not observed business activity.`, because this database is the
seeded one.

### The figures

The page, as the analyst, for the latest 90 days of sales:

```text
$ python f344_page_values.py
GET /reports/ as ANALYST -> 200 | headline figures: [('Revenue', '42,163.00'), ('Sales', '185')]
   Channel | Sales | Revenue
   call_center | 36 | 9,936.00
   physical_store | 37 | 8,838.50
   web | 39 | 8,284.25
   mobile_app | 37 | 7,625.25
   marketplace | 36 | 7,479.00

   Store | Sales | Revenue
   Store 2 | 7 | 2,746.25
   Store 17 | 6 | 2,742.00
   Store 11 | 6 | 2,269.50
   Store 26 | 6 | 2,269.50
   Store 3 | 7 | 1,934.00
   Store 18 | 6 | 1,897.50
```

And a query written separately, recomputing the amounts from `transaction_line` rather
than from the stored totals, over the 90 days ending at the newest sale:

```text
$ psql -d retail -f f344_revenue.sql
== revenue in the 90 days ending at the newest sale, recomputed from the lines (not the stored totals): all sales, then by channel
     split      | sales | revenue  
----------------+-------+----------
 all            |   185 | 42163.00
 call_center    |    36 |  9936.00
 marketplace    |    36 |  7479.00
 mobile_app     |    37 |  7625.25
 physical_store |    37 |  8838.50
 web            |    39 |  8284.25
(6 rows)
== and the five largest stores
   name   | sales | revenue 
----------+-------+---------
 Store 2  |     7 | 2746.25
 Store 17 |     6 | 2742.00
 Store 11 |     6 | 2269.50
 Store 26 |     6 | 2269.50
 Store 3  |     7 | 1934.00
(5 rows)
```

Revenue 42,163.00 over 185 sales, each channel, and the first five stores agree with the
page to the cent.

## Tests

```text
$ pytest tests/test_reports_index.py
19 passed in 0.40s
```

`test_the_placeholder_is_gone`, `test_the_revenue_summary_shows_totals_and_both_splits`,
`test_a_profile_is_offered_only_the_reports_it_can_open`,
`test_no_link_is_offered_that_the_profile_would_be_refused[...]`,
`test_a_profile_holding_only_report_read_still_gets_the_revenue_summary`,
`test_the_revenue_read_is_parameterized_and_window_bound`.

## What was not run

- `MARKETING` and `AUDITOR` were requested by the script and found to be offered the same
  four links as `ANALYST`; they were not captured.
- The empty state (`No sales yet`) was not shown: the database has sales.
- Nothing was run on the Compute Engine instance.

## The scripts

<details><summary><code>f344_reports.py</code> and <code>f344_page_values.py</code> (need the sign-in helper <code>mq.py</code>, listed in <a href="f7-04-f7-05-migration-and-matrix.md">F7-04 and F7-05</a>)</summary>

```python
import re, html
from mq import Client
ROLES = {"ADMIN": "admin@mosaiq-demo.com", "ANALYST": "user14@mosaiq-demo.com", "STORE_MANAGER": "user15@mosaiq-demo.com",
         "MARKETING": "user10@mosaiq-demo.com", "INVENTORY_PLANNER": "user11@mosaiq-demo.com",
         "AUDITOR": "user12@mosaiq-demo.com", "CUSTOMER": "user13@mosaiq-demo.com"}
for role, email in ROLES.items():
    c = Client(); c.login(email)
    s, h, t = c.get("/reports/")
    if s != 200:
        print(f"{role:18} GET /reports/ -> {s}")
        continue
    body = t[t.index("<main"):] if "<main" in t else t
    links = re.findall(r'<li><a href="([^"]+)">([^<]+)</a>', body)
    rev = re.search(r'Revenue</span>\s*<span class="mq-metric__value mq-num">([^<]+)<', body)
    placeholder = "still being built" in t
    print(f"{role:18} GET /reports/ -> {s}  revenue={rev.group(1) if rev else None}  placeholder-sentence={placeholder}  links={[l[1] for l in links]}")
    # every offered link must answer 200 for this same profile
    bad = [(href, c.get(href)[0]) for href, _ in links if c.get(href)[0] != 200]
    print(f"{'':18}   links that do not answer 200 for this profile: {bad}")
```

```python
"""What /reports/ shows as the analyst: the two headline figures and the first rows of each split, read from the HTML."""
import re
from html.parser import HTMLParser
from mq import Client

class Tables(HTMLParser):
    def __init__(self):
        super().__init__(); self.tables = []; self.row = None; self.cell = None
    def handle_starttag(self, tag, attrs):
        if tag == "table": self.tables.append([])
        if tag == "tr": self.row = []
        if tag in ("td", "th"): self.cell = ""
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(" ".join(self.cell.split())); self.cell = None
        if tag == "tr" and self.row is not None and self.tables:
            self.tables[-1].append(self.row); self.row = None
    def handle_data(self, d):
        if self.cell is not None: self.cell += d

c = Client(); c.login("user14@mosaiq-demo.com")
s, h, t = c.get("/reports/")
body = t[t.index("<main"):]
metrics = re.findall(r'mq-metric__label">([^<]+)</span>\s*<span class="mq-metric__value mq-num">([^<]+)<', body)
print("GET /reports/ as ANALYST ->", s, "| headline figures:", metrics)
p = Tables(); p.feed(body)
for tbl, n in zip(p.tables, (None, 7)):
    for row in tbl[: (n or len(tbl))]:
        print("   " + " | ".join(row))
    print()
```

</details>

<details><summary><code>f344_revenue.sql</code></summary>

```sql
\pset pager off
\echo '== revenue in the 90 days ending at the newest sale, recomputed from the lines (not the stored totals): all sales, then by channel'
with w as (select max(occurred_at) as latest from transaction),
t as (select tr.* from transaction tr, w where tr.occurred_at > w.latest - interval '90 days'),
lines as (select t.transaction_id, sum(l.quantity*l.unit_price) as amount from t join transaction_line l using(transaction_id) group by 1)
select 'all' as split, count(*) as sales, sum(lines.amount) as revenue from t join lines using(transaction_id)
union all
select c.name, count(*), sum(lines.amount) from t join lines using(transaction_id) join channel c using(channel_id) group by c.name
order by 1;
\echo '== and the five largest stores'
select s.name, count(*) as sales, sum(l.amount) as revenue from transaction t join (select transaction_id, sum(quantity*unit_price) amount from transaction_line group by 1) l using(transaction_id) join store s using(store_id) where t.occurred_at > (select max(occurred_at) from transaction) - interval '90 days' group by s.name order by 3 desc, 1 limit 5;
```

</details>

# F12-01 — Highcharts segmentation dashboard

Evidence that the state of the segmentation is visible without assembling four
reports: `/segmentation-dashboard/` shows one run's segment sizes, its RFM
distribution, its migration against the previous run and its revenue by label, all
keyed on the stable label code, rendered from data embedded in the server-rendered
page rather than fetched afterwards, and refused by the default-deny gate to any
profile without `segment.read`.

Covers the six acceptance criteria on F12-01 and business rule RN-41. It reuses F7's
migration comparison and F9's label vocabulary unchanged; the two new reads and the
four chart-building functions are this story's own. No schema change, nothing here
writes.

## A real risk this evidence exists to rule out

`docs/design-system/charts/index.html`, the worked example ADR-0002 ships, loads
Highcharts from `unpkg.com`. That is not available to this page. Reading
`deploy/nginx/mosaiq.conf`:

```
map $uri $mosaiq_script_sources {
    default "'self'";
    ~^/docs/design-system/charts/ "'self' 'unsafe-inline' https://unpkg.com";
}
```

The CDN allowance, and `'unsafe-inline'`, are scoped to the design system's own
example page. Every application page, this one included, carries `script-src 'self'`
only. This is exactly the failure this repository already had once, the same way,
with fonts: `docs/design-system/README.md` records that a CSS `@import` from
`fonts.googleapis.com` "blocked it: production rendered in system fonts for the
whole of #68 while every local check passed, because the compose proxy sends no
CSP." A dashboard built against the worked example's CDN tags would pass every test
in this sandbox, where nothing enforces the real policy, and silently fail to render
a single chart in production.

**What this story does about it.** Highcharts 11.4.8 (the version the worked example
itself pins) is vendored under `web/static/vendor/highcharts/`, exactly as the fonts
are vendored under `web/static/css/mosaiq/fonts/`. The MOSAIQ theme file is mirrored
byte-for-byte from `docs/design-system/charts/mosaiq-highcharts-theme.js` to
`web/static/css/mosaiq/charts/`, matching how `styles.css` and the rest of the design
system are already mirrored into the static tree. Every `<script>` tag on the page
has a same-origin `src=`; the chart data sits in a `<script type="application/json">`
block, which is inert data and not a script the policy has any opinion about,
because it never executes.

## How this run was produced

Against the database the three ordered scripts build, with the application run as
the restricted role `retail_app`, real `RFM_RULES` and `KMEANS` runs made through the
application's own code, and the page opened in **headless Chromium**, not merely
rendered by the test client, specifically so a real CSP-equivalent boundary (no
cross-origin requests were ever permitted to succeed) and real Highcharts execution
could be checked.

## Criterion 1 — segment sizes, RFM distribution, migration flow, revenue by label

The first run made, #34 (`RFM_RULES`), has no earlier run to compare against, so the
capture below also documents that honestly stated edge case:

| | |
|---|---|
| 1440 px | [`f12-01-dashboard-1440.png`](f12-01-dashboard-1440.png) |
| 375 px | [`f12-01-dashboard-375.png`](f12-01-dashboard-375.png) |

*Segment sizes* and *Revenue by label* are column charts over all six labels plus
*Unassigned*, each a zero-height bar where a label holds nobody or earned nothing.
*RFM distribution* is a 5×5 heatmap, recency by frequency, coloured by customer
count. *Migration flow* states plainly: *"There is no earlier run to compare this one
against."*

A second run, K-means run #35, does have a previous run (#34) to compare against,
which exercises the sankey and shows the two methods compared directly:
[`f12-01-kmeans-run-1440.png`](f12-01-kmeans-run-1440.png). Its *Migration flow* shows
customers moving from run #34's rule-based labels to run #35's K-means labels — for
example, most of *Lost*'s customers move to *Potential*, *Loyal* or *Champion* — and
*New to the population* / *Left the population* both read 0, since both runs scored
the same 30 customers.

## Criterion 2 — the chart data is embedded, never fetched

The whole point of the exercise above: every request Chromium made while the
dashboard was open was captured.

```
external (non-same-origin) requests made by the page: []
console/page errors: []
  #mq-chart-sizes: rendered an SVG chart = True
  #mq-chart-revenue: rendered an SVG chart = True
  #mq-chart-rfm: rendered an SVG chart = True
  #mq-chart-migration: rendered an SVG chart = True
```

Zero external requests, zero console errors, and all four containers hold a real
Highcharts SVG. `tests/test_segmentation_dashboard_route.py` pins this from the
markup itself: every `<script>` tag except the one `application/json` block carries
a same-origin `src=`, and neither `unpkg.com` nor any `cdn.` domain appears on the
page.

## Criterion 3 — every chart is keyed on label, none reads the method

**On the real database**, the embedded JSON for run #34 was read back and compared
with independently written SQL that fills in every vocabulary label, including the
ones nobody holds:

```
sizes match SQL: True   {'CHAMPION': 7, 'LOYAL': 0, 'POTENTIAL': 6, 'AT_RISK': 0, 'HIBERNATING': 0, 'LOST': 17, None: 0}
revenue match SQL: True {'CHAMPION': 20027.5, 'LOYAL': 0.0, 'POTENTIAL': 16802.5, 'AT_RISK': 0.0, 'HIBERNATING': 0.0, 'LOST': 33370.0}
heatmap total == scored customers: True (30 == 30)
previous run picked: 34 == SQL: 34
```

For run #35, the migration weights were checked against the customers both runs
actually share: `migration total weight == customers compared: 30 == 30`.

**The code itself never branches on method.** `web/services/segmentation_dashboard.py`
and `web/db/segmentation_dashboard.py` contain neither `kmeans` nor `rfm_rules`,
checked by a test that reads the files; `get_previous_run`'s statement selects
`r.method` only to describe a run (as every `SegmentationRun` reader already does),
and a test asserts it is never a `WHERE` condition. The RFM heatmap's own quintiles
are recomputed from raw recency and frequency rather than read from the stored
`r_score`/`f_score`, which are populated for only one of the two methods (F9-02); a
test builds the same rows under an `RFM_RULES` run and a `KMEANS` run and requires
identical charts.

## Criterion 4 — the `Synthetic` label

Visible on every capture above: a pill reading *Synthetic* with the text *"These
figures come from demonstration data, not observed business activity."* This is
`Config.data_is_synthetic`, defaulting to true, tested in `tests/test_config.py` and
`tests/test_segmentation_dashboard_route.py` for both states.

**This needed a decision, and it is flagged here for the team rather than settled
quietly.** ADR-0019's `Synthetic` label is a `data_origin` column on the `experiment`
table; no table this dashboard reads carries any such marker, and no schema change is
in this story's scope. The badge is therefore an **instance-level** flag, not a
per-row fact: every instance shown running today, including the one every other
Phase 9/10 story in this delivery was verified against, holds only the seeded
demonstration data (`sql/02_seed_30_per_table.sql`), so the default is on. A
deployment with real accepted sales and real segmentation runs sets
`DATA_IS_SYNTHETIC=false`. **This is the same kind of open decision as F8-04's and
F10-02's `STORE_MANAGER` question** — recorded, not guessed past.

## Criterion 5 — a profile without the permission is refused

```
Signed in as INVENTORY_PLANNER -> 403
```

Captured: [`f12-01-refused-inventory-planner-1440.png`](f12-01-refused-inventory-planner-1440.png).
`CUSTOMER` is refused the same way. **`STORE_MANAGER` is not**: this delivery's
`develop` now grants `STORE_MANAGER` the `segment.read` permission, which it did not
hold when F8-04 and F10-02 were evidenced. That resolves the open point those two
stories raised, and this story's tests reflect it (`ADMIN`, `ANALYST`, `MARKETING`,
`AUDITOR` and `STORE_MANAGER` all reach 200; `INVENTORY_PLANNER` and `CUSTOMER`
reach 403). The route is in the negative-flow matrix, so the anonymous refusal and a
`405` on `POST` are covered too.

## Criterion 6 — 375 px and 1440 px

Horizontal overflow, measured in the browser as `scrollWidth - clientWidth`, was
**0 px** at both widths for every capture. At 375 px the four chart panels stack into
one column. Highcharts' own responsive behaviour, which the design system's worked
example documents ("thins x labels to every second tick" below 480 px), thins the
category labels under *Segment sizes* and *Revenue by label`; every bar, its colour
and its height remain, so the charts stay legible without a label on every tick.

## When there is no run at all

The database's segmentation history was cleared (inside a transaction later
restored) to capture the genuinely empty state, signed in through the normal seeded
accounts:

| | |
|---|---|
| 1440 px | [`f12-01-no-runs-1440.png`](f12-01-no-runs-1440.png) |
| 375 px | [`f12-01-no-runs-375.png`](f12-01-no-runs-375.png) |

*No segmentation run yet* is shown, no chart container is rendered, and the
`Synthetic` badge still shows, since it describes the instance and not any one run.

## The tests catch a broken dashboard

Faults were seeded into the service, the database reads and the route, one at a
time, and each was required to make a test fail. All did, across three files
(`tests/test_segmentation_dashboard.py`, `_db.py`, `_route.py`):

| Layer | Faults seeded | Each caught by |
|---|---|---|
| Service | A zero-count label omitted; an *Unassigned* revenue bar invented; heatmap cells dropped; unassigned customers left in the heatmap; unchanged migrations dropped from the flow; population changes charted as flows; the revenue window's start miscalculated; the previous run never looked up; ties broken by descending id | at least one test each |
| Database reads | The R/F/M read paged with a `LIMIT`; the previous-run read restricted by method | at least one test each |
| Route | The permission relaxed; a bad run id accepted; an unknown run id not answering 404 | at least one test each |

One fault in the service, `quintile_bins` failing to reach bin 1 with fewer than five
customers, was found by the tests themselves rather than seeded: PostgreSQL's own
`ntile(5)` behaves the same way (the first `n` buckets each take one row and the rest
stay empty), and the fix was to match that exactly rather than invent a spread that
always reaches both extremes — recorded in RN-37 already, and pinned again here by a
test that reproduces it with 3 scored customers.

## Tests

`tests/test_segmentation_dashboard.py` (32), `tests/test_segmentation_dashboard_db.py`
(13) and `tests/test_segmentation_dashboard_route.py` (26) are new — 71 tests, plus
two new assertions in `tests/test_config.py` for `data_is_synthetic`. Two existing
tests changed only because they enumerate what exists: the menu lists in
`tests/test_authz.py` now include *Segmentation dashboard*, and the negative-flow
matrix now includes `/segmentation-dashboard/`.

```
$ pytest -q
1541 passed         # 1468 on develop, plus 73
$ black --check .   # All done
$ ruff check .      # All checks passed!
```

## Things to know

* **The dashboard reads the whole run's rows unpaged**, same as F9-04's model
  comparison, fine at this delivery's customer counts and the first thing to change
  for a run of many thousands.
* **The run picker offers only the newest 100 runs**, with an older one chosen by
  `?run=` added to the list so it stays selected, the same pattern the migration
  matrix and model comparison pages already use.
* **The heatmap re-bins recency and frequency for every request**; it is not stored
  anywhere, so a K-means run's heatmap and an RFM_RULES run's are computed the same
  way and can be compared like for like.
* **A dependency was added deliberately**: Highcharts is vendored at
  `web/static/vendor/highcharts/` (11.4.8, matching the design system's own pin), and
  its license is the one already accepted when ADR-0002 and the roadmap's Phase 12
  scope committed the team to Highcharts; this story adopts that choice rather than
  making it.

## Not evidenced here

Filtered segment-history and migration reports (F12-02), consumption-shift and
recommendation reports (F12-03), and campaign/experiment reports (F12-04).

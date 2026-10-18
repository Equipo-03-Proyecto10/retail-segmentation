# AN-03 — Experiment results on a Highcharts dashboard

Evidence for AN-03 (#415): one experiment's conversion rate per arm, its
difference from the baseline arm with the 95% interval, and conversions over
time inside the conversion window, drawn from data embedded in the server-
rendered page, with Highcharts served by the application.

No schema changed, nothing is written, no dependency was added. The page reads
`experiment`, `experiment_group`, `experiment_assignment`, `experiment_exposure`
and `experiment_conversion` (and `transaction`, through the existing conversion
reads).

Checked from the commit that adds `web/routes/experiment_dashboard.py` against
PostgreSQL 16.15, loaded with `sql/00_create_database.sql`, `01_schema.sql` and
`02_seed_30_per_table.sql`, then served as `retail_app` and driven with
Chromium. The seed has four assignments per experiment, too few to chart, so
three experiments were added to that sandbox database only (not committed):

| Experiment | Origin | Arms | Window | Assigned |
|---|---|---|---|---|
| 101 Win-back offer | `OBSERVED` | Holdout (control), 10% voucher | 14 days | 300 + 300 |
| 102 Two offers head to head | `SEEDED` | Free shipping, Double points (no control) | 7 days | 200 + 200 |
| 103 Loyalty bonus | `INJECTED` | Holdout (control), Loyalty bonus | 28 days | 125 + 125, windows still open |

Conversions were attributed with the same statement `evaluate_conversions`
runs. Treatment arms were exposed for 80% of their customers.

## The figures against independent SQL

`build_dashboard` was run against the real database and each figure compared
with a query written separately (counts straight from the three event tables;
the interval recomputed by hand; the over-time series from a day histogram):

```text
experiment 101 [OBSERVED] label=None
  arm 1001 Holdout:      assigned=300 conv=30 exposed=0   exp_conv=0   ITT=0.1   OK
  arm 1002 10% voucher:  assigned=300 conv=45 exposed=240 exp_conv=44  ITT=0.15  OK
  10% voucher: +5.00 pts  [-0.28, +10.28] p=0.0641 OK
  over time: by day 3, control 8 (sql 8), treatment 19 (sql 19); last point = ITT  OK
experiment 102 [SEEDED] label=Synthetic   baseline: reference Free shipping
  Double points: +4.00 pts  [-1.87, +9.87] p=0.1824 OK
experiment 103 [INJECTED] label=Synthetic
  Loyalty bonus: +8.00 pts  [-1.03, +17.03] p=0.0845 OK
ALL OK
```

The 101 treatment arm shows 15.00% over everyone assigned (45 of 300) and
18.33% over the exposed (44 of 240): different numerators and denominators,
shown as two bars, not one.

## In a real browser

Signed in as an `ANALYST`, each experiment was opened at 1440 px and 375 px.

| Check | Result |
|---|---|
| Highcharts containers drawn | 3 of 3, every page, both widths |
| Horizontal page scroll | none at 375 px or 1440 px |
| Requests outside the application | none; console errors none |
| `OBSERVED` (101) | no `Synthetic` pill or subtitle anywhere |
| `SEEDED` and `INJECTED` (102, 103) | `Synthetic` in each chart's header, in each chart's own subtitle and in its accessible name; the page-level alert too |
| No control (102) | chart and table titled *Difference from the reference arm*, with the ADR-0028 explanation; never *Uplift against the control* |
| Windows still open (103) | the *Preliminary* alert, 210 customers |
| `STORE_MANAGER` (no `campaign.read`) | 403 at both widths |

At 375 px the first capture showed the second arm's name missing under the rate
chart: the theme's phone-width rule keeps every second axis label. The page
script now adds a rule after the theme's that shows every arm's name; the
recheck shows both. The arms table scrolls inside its own wrapper at 375 px
rather than widening the page.

Captures: [observed, 1440](an-03-observed-1440.png) ·
[observed, 375](an-03-observed-375.png) ·
[no control, Synthetic, 1440](an-03-no-control-synthetic-1440.png) ·
[no control, Synthetic, 375](an-03-no-control-synthetic-375.png) ·
[preliminary, Synthetic, 375](an-03-preliminary-synthetic-375.png) ·
[refused, store manager, 1440](an-03-refused-store-manager-1440.png).

## Tests

43 new tests (`tests/test_experiment_dashboard.py` for the arithmetic and
refusals, `tests/test_experiment_dashboard_route.py` for the page), and the
route's row in `tests/test_analytics_authz.py`, `tests/test_authz.py` (menu) and
`tests/test_negative_flows.py`. Six faults were seeded one at a time and each
was caught: a non-cumulative series, the wrong reference arm, an unescaped `<`
in the embedded JSON, a chart header without its `Synthetic` pill, the exposed
count taken from the assigned one, and the gate changed to `segment.read`.

The clean `develop` zip passes 2955 tests excluding `tests/test_contracts_check.py`
(whose 16 tests need the `oasdiff` binary, absent from this sandbox, with or
without this change); with this change, 3009.

## Decision recorded, not made silently

The issue asks for uplift "against the reference arm when there is no control
(ADR-0028)". ADR-0028 says the opposite of an uplift there: the uplift service
refuses a no-control experiment, and nothing is computed against "everyone
else". The dashboard therefore shows, for a no-control experiment, a labelled
*difference between two treatment arms* against the arm defined first, and
leaves the uplift service and its refusal untouched (RN-57). If the team would
rather the dashboard refuse too, it is one branch in `_differences`.

## What was not run

The sandbox seed is not the deployed instance's data. Nothing was run on the
Compute Engine instance. Experiments 101 to 103 exist only in the sandbox.

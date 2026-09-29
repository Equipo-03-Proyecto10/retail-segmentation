# F12-04 — Campaign and experiment report

Evidence for F12-04 (#230): the report keeps assignment, exposure and
conversion as separate counts, reuses F11-07's intent-to-treat uplift, combines
campaign and data-origin filters, refuses values it did not offer, and exports
the same selection with explicit provenance.

No schema changed. The checks ran from commit `01e135d` on the assigned Compute
Engine instance, `mosaiq-deployment-vm`, against PostgreSQL 18.6. A temporary
database was loaded from the committed schema and seed scripts and the PR
checkout was served on a loopback-only port through an SSH tunnel. The deployed
application and its `retail` database were never replaced or changed.

## Real PostgreSQL query and export

Experiment 3's conversions were evaluated first. The evaluation recorded seven
qualifying sales in the clean seed. The report was then built with
`data_origin=INJECTED`; this exercised the report's CTE and its parameterized
`ANY(%s)` array twice, for the page of experiment ids and the group aggregation.

```text
filtered_total=10 page_rows=10
experiment=3 groups=[('CONTROL', 2, 0, 2, 0, 0),
                     ('TREATMENT', 2, 1, 2, 0, 0)]
uplift_arms=1 refusal=None
csv_rows=20 origins=['INJECTED'] labels=['Synthetic']
```

Each group tuple is `(kind, assigned, exposed, converted, pending, unrecorded)`.
The control remains never exposed while its assigned customers stay in the
uplift denominator. Both arms had complete conversion counts, so the report
measured one treatment comparison. All twenty exported arm rows retained the
selected origin and the literal `Synthetic` label.

A syntactically valid but unoffered campaign id was also sent to both real
routes after authentication. Neither became a misleading empty report:

```text
unoffered_page=400 unoffered_export=400
```

## Rendered report

The browser selected campaign 3 and `INJECTED`. The page showed only Experiment
3, its provenance warning and label, the three separately named counts, and the
intent-to-treat uplift with its 95% interval. The matching CSV response was 200
and contained both `INJECTED` and `Synthetic`.

| Viewport | Capture |
|---|---|
| 1440 px | [Filtered experiment report](f12-04-experiment-report-1440.png) |
| 375 px | [Filtered experiment report](f12-04-experiment-report-375.png) |

Headless Chromium measured zero page-level horizontal overflow at both widths.
At 375 px the wide result table scrolls inside its panel while the filters,
provenance warning, explanation and pagination stay within the viewport.

## Automated coverage

`tests/test_experiment_report.py` covers separate denominators,
intent-to-treat uplift, incomplete-count refusal, provenance, formula-injection
defence, parameterized and combined filters, an unoffered numeric campaign,
pagination, the rendered page, CSV download and authorization. The protected
routes also remain in the global negative-flow matrix.

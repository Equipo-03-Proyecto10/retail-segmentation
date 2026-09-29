# F11-07 — Intent-to-treat uplift validation

Evidence for F11-07 (#226): uplift compares every assigned customer in each
treatment arm with the control, the A/A check reads a population fixed before a
cut-off and outcomes from real later `transaction` rows, the injected fixture
recovers the stated five-point effect, incomplete counts are refused, and
seeded or injected output carries the literal `Synthetic` label.

No schema changed. The checks below ran on the assigned Compute Engine instance,
`mosaiq-deployment-vm`, with PostgreSQL active. The A/A check was read-only
against the instance's existing `retail` database. Browser evidence used a
temporary database loaded from the committed schema and seed scripts, served by
the PR checkout on a loopback-only port through an SSH tunnel; it never replaced
the deployed application.

## Intent to treat and refusal boundaries

The measurement reads assignment and recorded conversion counts per arm.
Exposure is not an input. A customer assigned but never exposed therefore
remains in the denominator.

The result is refused instead of silently becoming incomplete when any of these
holds:

- the experiment has no control or no assignments;
- the control or any treatment arm has no assigned customer;
- a qualifying sale has not yet been recorded as an
  `experiment_conversion`;
- the target metric is not `CONVERSION`.

The empty-treatment case matters when a campaign population is smaller than
the number of configured arms. The unrecorded-sale case integrates F11-06's
distinction between a durable conversion and a sale that merely qualifies for
the next evaluation.

## A/A over real later transactions

The instance held 300 transactions for 30 distinct customers, from
2026-04-02 through 2026-09-28 UTC. `run_aa_validation` was run from commit
`20e7a20` with cut-off `2026-06-01T00:00:00+00:00`, a 14-day outcome
window and the fixed default seed.

Population membership came only from transactions before the cut-off.
Conversion came only from transactions in the later half-open interval. The
deterministic split produced equal arms and no significant difference at alpha
0.05:

```text
control:   15 assigned, 6 converted
treatment: 15 assigned, 8 converted
uplift:    +13.3333 percentage points
p-value:   0.464214
significant: False
```

The same real population was also checked at 7 and 30 days, and at cut-offs in
June, July, August and September. All twelve combinations were non-significant;
the record above is the stated reproducible case.

## Injected uplift

The fixed fixture uses 10,000 assignments per arm with exact seeded counts.
The implementation recovered the injected five percentage points, and its 95%
interval excluded zero:

```text
control:   10000 assigned, 1000 converted
treatment: 10000 assigned, 1500 converted
uplift:    +5.0000 percentage points
95% CI:    +4.0859 to +5.9141
interval excludes zero: True
```

## Rendered result and provenance

Experiment 3 in the clean seeded database has data origin `INJECTED`.
Conversions were evaluated first, so uplift was not computed from the three
qualifying sales that initially remained unrecorded. The rendered page then
showed the control and treatment denominators, rates, uplift, interval and
p-value, with `Synthetic` both as a badge and in the warning.

| Viewport | Capture |
|---|---|
| 1440 px | [Uplift result](f11-07-uplift-1440.png) |
| 375 px | [Uplift result](f11-07-uplift-375.png) |

A headless Chromium assertion measured zero page-level horizontal overflow at
both widths. At 375 px the result table scrolls inside its own panel while the
page, warning, explanatory text and actions remain within the viewport.

## Automated coverage

`tests/test_experiment_uplift.py` covers the arithmetic, deterministic A/A
split, database-read parameters, injected fixture, intent-to-treat denominator,
synthetic provenance, refusal paths, incomplete counts and the rendered page.
The protected route also remains in the global negative-flow matrix.


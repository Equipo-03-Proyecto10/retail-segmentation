# #343 — conversion among exposed customers, and uplift without a 409

Run on a real PostgreSQL 16 (Docker), a clean load of the three scripts from this
branch, the application connected as `retail_app`, seeded data (30 experiments).

## The per-exposure count against an independent query

For all 60 groups of the 30 seeded experiments, the exposed and bought-after-exposure
counts from `list_report_groups` (the report) and `list_exposed_conversion` (the
uplift page) equal a separately written query. That comparison is empty on the seed,
which has no sale after an exposure, so the boundaries were exercised in a transaction
that was rolled back, on three exposed customers:

| Sale inserted | Counted |
|---|---|
| One day after the first exposure, inside the window | yes (0 → 1) |
| One day before the first exposure | no (0 → 0) |
| Exactly at first exposure + window | no (0 → 0) |

## The pages, driven in Chromium

- `/experiments/1/uplift` before conversion is evaluated: **200**, with "Conversion not
  evaluated yet" and a link to the conversion page (was a 409).
- After "Evaluate conversion", the page renders both tables. On experiment 1 the two
  rates differ, as the issue expects: 100.00% intent to treat, 0.00% per exposure (the
  seeded conversions are attributed from assignment, and no sale follows the exposure).
- The report shows "Rate (assigned)" and "Rate (exposed)" per arm.

Captures at 1440 px and 375 px: `f343-uplift-not-evaluated-*.png`,
`f343-uplift-evaluated-*.png`, `f343-report-*.png`.

## Found while checking

The uplift page still said "the exposure rate is a delivery diagnostic and plays no
part here" under the new per-exposure table, which read as a contradiction. It now says
how many customers were exposed plays no part in the uplift.

## Still open

The report page at 375 px is long: each experiment's caption is now a paragraph. It
scrolls its tables inside their wrappers and nothing overflows the page, but a shorter
caption would read better on a phone.

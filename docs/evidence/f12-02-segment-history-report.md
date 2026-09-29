# F12-02 — Filtered segment history and migration reports

Evidence that a question about a specific segment or period can be answered without
a new report: `/segment-history-report/` lists every `customer_segment_history` row
across every run, filterable by run, label and period at once, each row expandable
to the R/F/M explanation of why that customer holds that label, says so plainly
when a filter combination matches nothing, and is refused by the default-deny gate
to any profile without `segment.read`.

Covers the four acceptance criteria on F12-02 and business rule RN-42. It reuses
F7-06's `explain_migration` and F12-01's `get_previous_run` unchanged; the two new
reads, the filter orchestration and the page are this story's own. No schema
change, nothing here writes.

## What this delivers

| | Where |
|---|---|
| Two reads: filtered, paginated history rows, and their count | `web/db/segment_history_report.py` |
| Filter orchestration and the per-row explanation | `web/services/segment_history_report.py` |
| The page: `/segment-history-report/`, gated on `segment.read`, under Analysis | `web/routes/segment_history_report.py`, `web/templates/segment_history_report/index.html` |

Each row's explanation is F7-06's own `explain_migration`, given this row and the
customer's assignment on the run immediately before this row's run — found by
`web.db.segmentation_dashboard.get_previous_run`, built for F12-01 and reused here
rather than re-implemented, so "the previous run" means the same thing on both
pages.

## A real bug this evidence caught

The first attempt against real PostgreSQL answered every request with a 500:

```
psycopg.errors.AmbiguousParameter: could not determine data type of parameter $2
LINE 7:                   $2 IS NULL
```

The label filter's parameter appears only in `IS NULL` and text-equality
comparisons, and PostgreSQL could not infer its type from that alone. The fix is
the same explicit cast `web.db.audit`'s own filters already use
(`%(entity)s::text IS NULL`), applied to `%(label_code)s`. The mocked unit tests
could not have caught this: they assert on the statement's text, not on how
PostgreSQL parses it, which is exactly why this delivery's stories are checked
against a real database before being called done.

## A second thing the sandbox alone could not have caught

Building the `WHERE` clause once as a shared constant and dropping it into both
statements is refused elsewhere in this codebase:

```
AssertionError: PosixPath('web/db/segment_history_report.py')
assert not True
 where True = isinstance(<ast.JoinedStr object at ...>, (<class 'ast.JoinedStr'> | <class 'ast.BinOp'>))
```

`tests/test_write_services.py` walks every file under `web/` and refuses any
`.execute()` call whose SQL text is built with an f-string or concatenation,
categorically, so that a future edit cannot quietly turn a "safe" shared constant
into a live injection point. The fix follows `web.db.audit`'s own established
pattern: the `WHERE` clause is a literal, in full, in both `list_history_entries`
and `count_history_entries`, and a new test,
`test_the_two_statements_filter_identically`, compares the two clauses' source
text so they cannot drift apart silently the way two independently maintained
clauses otherwise could.

## How this run was produced

Against the database the three ordered scripts build from empty, with the
application run as the restricted role `retail_app`. Four real `RFM_RULES` runs
were made through the application's own code; between the third and the fourth, one
real customer was given six large purchases, so the fourth run's evidence shows a
genuine, non-invented label change rather than a synthetic one.

```
run 31: processed=30 reassigned=29
run 32: processed=30 reassigned=0
run 33: processed=30 reassigned=0
run 34: processed=30 reassigned=2   <- after the extra purchases
```

## Criterion 1 — every applied filter is reflected

```
total rows, unfiltered, SQL: 1020 | page showed: 1020
rows for run 34, SQL: 30 | page showed: 30
LOST rows in 2026-01-01..2026-03-01, SQL: 0 | page showed: 0 (No rows match)
```

1,020 rows is 34 runs × 30 customers, confirming the unfiltered read genuinely spans
every run rather than the newest one. Filtering to run #34 alone narrows to exactly
that run's 30 rows.

| | |
|---|---|
| Unfiltered, 1440 px | [`f12-02-report-1440.png`](f12-02-report-1440.png) |
| Unfiltered, 375 px | [`f12-02-report-375.png`](f12-02-report-375.png) |
| Filtered to run #34 | [`f12-02-filtered-run-1440.png`](f12-02-filtered-run-1440.png) |

The chosen run and label are reflected back into the form's own `<select>`
elements (`tests/test_segment_history_report_route.py` asserts the `selected`
attribute lands on the right option), so reloading or sharing a filtered link
reproduces the same filters.

## Criterion 2 — an expanded row shows the R/F/M explanation

*Demo Customer 1*'s row in run #34 was expanded — a native `<details>` disclosure,
no script involved:

[`f12-02-expanded-1440.png`](f12-02-expanded-1440.png)

It reads **LOST → POTENTIAL**, *"Monetary moved the most (score delta +3)"*, and a
table with Recency, Frequency and Monetary's raw values and scores before and
after. This was checked directly against `explain_migration`, called by hand with
this customer's actual rows:

```
label: LOST -> POTENTIAL
most_changed_caption: Monetary moved the most (score delta +3).
  Recency: raw 2026-09-28 02:13:00.570828+00:00 -> 2026-09-28 02:13:00.570828+00:00 | score 5 -> 5 (delta 0)
  Frequency: raw 6 -> 16 | score 5 -> 5 (delta 0)
  Monetary: raw 852.00 -> 6820.00 | score 2 -> 5 (delta 3)
```

Every figure matches the capture exactly. The "previous run" resolved to **#33**,
not #32 or #31 — the run immediately before #34, correctly, even though this
customer's label had already been `LOST` across three consecutive runs beforehand;
the explanation is against the *immediately preceding* run, not the last run where
the label differed.

## Criterion 3 — a filter combination with no rows says so

A label and a period chosen together to match nobody:

[`f12-02-no-rows-1440.png`](f12-02-no-rows-1440.png)

*No rows match those filters*, with the chosen filters still shown in the form, and
no table rendered — `tests/test_segment_history_report_route.py` asserts `<table`
is absent from that response.

## Criterion 4 — a profile without the permission is refused

```
Signed in as INVENTORY_PLANNER -> 403
```

Captured: [`f12-02-refused-inventory-planner-1440.png`](f12-02-refused-inventory-planner-1440.png),
with no *Segment history report* entry in the menu. `CUSTOMER` is refused the same
way; `ADMIN`, `ANALYST`, `MARKETING`, `AUDITOR` and `STORE_MANAGER` all reach 200.
The route is in the negative-flow matrix, so the anonymous refusal and a `405` on
`POST` are covered too.

## 375 px

[`f12-02-report-375.png`](f12-02-report-375.png). The page body itself never scrolls
sideways; the wide table scrolls inside its own `mq-table-wrap` container, the same
pattern every other table page in this delivery already uses:

```
table-wrap scrollWidth > clientWidth (scrolls internally): True
body scrollWidth == clientWidth (page itself does not scroll sideways): True
```

## The tests catch a broken report

Faults were seeded into the service and the database reads, one at a time, and each
was required to make a test fail. All did:

| Fault seeded | Caught by |
|---|---|
| The period's start-after-end check skipped | 1 test |
| The count read given different filters than the list read | 1 test |
| The previous-run lookup repeated per row instead of cached per run | 1 test |
| An explanation invented for a row with no previous run | 1 test |
| The Unassigned filter treated as no filter at all | 1 test |
| The period's last day left open-ended | 1 test |
| The pagination offset computed one page ahead | 2 tests |

## Tests

`tests/test_segment_history_report.py` (12), `tests/test_segment_history_report_db.py`
(15) and `tests/test_segment_history_report_route.py` (28) are new — 55 tests. Two
existing tests changed only because they enumerate what exists: the menu lists in
`tests/test_authz.py` now include *Segment history report*, and the negative-flow
matrix now includes `/segment-history-report/`.

```
$ pytest -q
1705 passed         # 1648 on develop, plus 57
$ black --check .   # All done
$ ruff check .      # All checks passed!
```

## Things to know

* **A run id that does not exist is not an error.** Unlike F12-01's dashboard or
  the migration matrix, this page does not need to resolve "the" run to render;
  filtering by a run id nobody used is just a filter no row matches, and the report
  says so the same way any other empty result does.
* **Expanding a row grows it in place**, standard `<details>` behaviour; the row's
  own cells do not repeat once expanded.
* **Every row does its own lookup of its customer's previous assignment**, cached
  per run rather than per row, so a page of 25 rows spanning a handful of runs
  costs a handful of extra reads, not 25.

## Not evidenced here

Consumption-shift and recommendation reports (F12-03), and campaign/experiment
reports (F12-04).

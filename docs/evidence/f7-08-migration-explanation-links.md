# F7-08 — The migration explanation, linked and in plain language (#338)

Evidence that a customer's migration explanation is reachable from wherever a
migration is shown, and reads as sentences a reader can act on: raw values with
recency in days, a changed/stable judgement by RN-50's thresholds, a score change
caused only by the quintile cut points named as such, and a customer new to the
later run shown as new rather than as `Unassigned → X`.

Covers the four acceptance criteria of #338 and business rule RN-50. No schema
change, nothing here writes.

## How it was produced

The stack was started from a clean database with `docker compose down -v` and
`docker compose up --build` on this branch, so the three scripts ran in order and
the seed loaded runs #1–#30. Screenshots were taken in Chrome, signed in as the
seeded demonstration administrator, using DevTools' device toolbar at 375 × 812
and 1440 × 900 and *Capture full size screenshot* (full page, saved at the
display's 2× pixel density). No page overflowed the viewport horizontally; wide
tables scroll inside their own wrapper, the pattern the rest of the application
uses.

To have a customer absent from the earlier run, one customer (*Grace Hopper*) with
one purchase was inserted into the local database with `psql`, and an RFM_RULES
run over 180 days (#31) was executed from the **Segment run** page. Adding a
scored customer moved the quintile cut points for others, which produced the
rank-only cases below without arranging them. The same scenario was first
reproduced on a throwaway PostgreSQL 16 container and captured with Playwright,
with identical labels, values and scores.

## What each screenshot shows

| Screenshot | Acceptance criterion | What to look for |
|---|---|---|
| `f7-08-explanation-moved-{375,1440}.png` | 2, 3 | *Demo Customer 19*, HIBERNATING → AT_RISK between runs #30 and #31 with all three values unchanged: every measure reads **stable**, the frequency sentence says its score moved because other customers moved the cut points, and the page says once, above the sentences, that the label changed without the customer's behaviour changing. Recency is in days with the purchase date, never a timestamp; unchanged scores read **stable**, not `+0`. |
| `f7-08-explanation-rank-only-{375,1440}.png` | 3 | *Demo Customer 7*, label unchanged (CHAMPION), 10 purchases in both runs, frequency score 4 → 5: the rank moved, not the customer. |
| `f7-08-explanation-new-customer-{375,1440}.png` | 4 | *Grace Hopper*, not part of run #30: **New customer**, first scored in run #31 as POTENTIAL. Nothing reads *Unassigned*; the earlier column says *Not in this run*. |
| `f7-08-customer-timeline-links-{375,1440}.png` | 1 | The customer timeline (#337): each label change carries **What changed** and **Explanation**; the current/previous panel summarises the move in one line and links to the explanation. |
| `f7-08-segment-change-{375,1440}.png` | 1, 2 | The segment-change page (#337) reuses the same sentences and links to the explanation. |
| `f7-08-matrix-cell-links-{375,1440}.png` | 1 | The migration matrix for runs #30 and #31 filtered to the **NEW** row: the new customer listed with its **View explanation** link (#339). |

## Checks

- `pytest`: 2646 passed.
- `black --check .` and `ruff check .`: clean.
- Each threshold's edge, the rank-only cases (recency, frequency, monetary), a
  K-means pair without scores, and absent versus unassigned are covered in
  `tests/test_migration_explanation.py`; the page in
  `tests/test_migration_explanation_route.py`; the links in
  `tests/test_customer_timeline_route.py`. Mutating each threshold comparison, the
  absent flag and the rank-only rule each failed at least two tests.

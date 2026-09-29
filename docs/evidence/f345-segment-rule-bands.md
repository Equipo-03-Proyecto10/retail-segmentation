# #345 — seeded segment rules produce every label

## Defect

`sql/02_seed_30_per_table.sql` generated `m_min = 1 + ((n*3-1) % 3)`, which is 3
for every row. With the lowest `segment_id` winning, the bands gave CHAMPION 27,
POTENTIAL 18 and LOYAL 15 of the 125 (R, F, M) triples and no rule for 65. Those
took the fallback in `web/db/segments.py` and were labelled LOST, so AT_RISK and
HIBERNATING could never be assigned and no migration into or out of them could occur.
The run page also said customers with no matching rule were "left unassigned" and
showed 0 for "Matched no rule", both untrue.

## Change

- Thirty literal bands that partition the triples, five per label (RN-46).
- The scoring statement returns `via_fallback`; the run page shows "No sales in
  window" (unassigned, RN-21) and "Matched no rule" (fallback count) separately.

## What was run

`tests/test_seed_segment_rules.py` reads the seed as text and checks, for all 125
triples, exactly one band matches; every label and every segment is reachable. It
fails on the previous seed (5 failures) and passes on this one.

## What was not run

No PostgreSQL is available where this was written, so the seed and the
`via_fallback` expression were **not** executed against a database here. CI's
"SQL scripts run clean from empty" job loads the seed. Confirm on a clean load with
the reproduction query from the issue, which should now return no `NO RULE` row.
The 375 and 1440 px captures of the run page are still owed (`docs/process.md` §6).

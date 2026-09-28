# F8-05 — Channel, store and category shifts

Evidence that a change in what a customer mostly buys through, where and of is
noticed when it happens: `detect_shifts` compares two stated periods and reports,
per customer, a changed dominant channel, dominant store or leading category with
both values, reports absence as absence, and reports nothing for a customer who did
not change.

Covers the four acceptance criteria on F8-05 and business rule RN-36. It builds on
the consumption profile (F8-03), whose ranking rules it reuses, and it has no page:
reporting shifts on screen is F12-03. No schema change, no new permission, no new
dependency, and nothing here writes.

## How this run was produced

Against the database the three ordered scripts build from empty
(`sql/00_create_database.sql`, `01_schema.sql`, `02_seed_30_per_table.sql`), then
`detect_shifts` called from Python with the application's own code. Every figure it
returned was recomputed by a separately written set of `SELECT` statements and
compared.

## The seeded database, two consecutive 90-day periods

```
earlier: 2026-04-01 -> 2026-06-30 | later: 2026-06-30 -> 2026-09-28
compared=30 shifted=12 unchanged=18 absent=0
   0009 {'category': ('Small electronics', 'Dairy')}
   0019 {'category': ('Milk', 'Dairy')}
   0020 {'category': ('Milk', 'Dairy')}
   0021 {'category': ('Sweet bread', 'Snacks')}
```

Cross-check against an independent recomputation, for every customer:

```
shifted customers  agree: True (12)
absences           agree: True (0)
compared           agree: True (30)
reconciles: compared = shifted + unchanged; 30 + 0 absent = 30 customers with sales
```

The seed has only category shifts: each seeded customer buys through one channel and
at one store throughout. The next section builds customers that exercise the other
two.

## The acceptance criteria, one by one

Customers below were created inside a transaction that is rolled back, over two
periods that are stated explicitly (`2026-01-01 → 2026-02-01` and
`2026-02-01 → 2026-03-01`).

**A change is reported with both values.** A customer whose channel, store and
category all differ between the periods:

```
Mover    (all three changed)    -> {'channel': (1, 2), 'store': (1, 2), 'category': (1, 2)}
```

**No shift is reported for a customer who did not change.** A customer buying the
same way in both periods has no entry, and is counted in `unchanged`:

```
Steady   (nothing changed)      -> not reported
```

**Absence is reported as such and not as a shift.** A customer with sales in only
one period is absent from the other, and is not compared:

```
OnlyEarlier                     -> later period empty; shifted? False
OnlyLater                       -> earlier period empty; shifted? False
```

**The periods are stated on the result.** They are arguments, and the report
carries them, in order:

```
the result states its periods: 2026-01-01 -> 2026-02-01 | 2026-02-01 -> 2026-03-01
```

## Two cases the criteria do not name, and what was decided

**A sale at the instant the periods meet.** Periods are half-open, `[start, end)`,
so a purchase at exactly `2026-02-01 00:00` belongs to the later period only. That
customer, who bought nothing else, is reported absent from the *earlier* period:

```
Boundary (sale at the shared instant) -> absent from: earlier (counted in the later period only)
```

Had the periods been closed at both ends the same sale would have counted on both
sides, and the customer would have appeared as compared and unchanged.

**A purchase with no product lines.** A customer whose earlier period holds only such
a purchase has no leading category there, so there is nothing to compare and no
category shift is reported:

```
NoLines (no lines in earlier)   -> category shift: None | reported at all: False
```

## Bad periods are refused before anything is read

```
overlapping      refused: The periods overlap, so a sale could count on both sides. Choose two periods where the earlier ends no later than the later begins.
empty period     refused: A period must end after it starts.
naive datetimes  refused: A period must carry a timezone at both ends.
```

`tests/test_consumption_shift.py` asserts that none of the three reads runs when the
periods are refused.

## "Dominant" means what it means on the profile

The dominant channel and store, and the leading category, are ranked by the same
functions the consumption profile uses (RN-35), with the same tie-breaks: most
purchases, then highest spend, then lowest id. The unit tests feed the same tied
rows through both and require the same answer, and require it under every
permutation of the input. A tie that resolves differently in the two periods
because the spend moved is a change under that rule, and the report says so.

## Quality gates

```
$ pytest -q
1072 passed         # 1020 with F8-04, plus 52 in tests/test_consumption_shift.py
                    # and tests/test_consumption_shift_db.py
$ black --check .   # All done
$ ruff check .      # All checks passed!
```

The reads are three `SELECT` statements per period, one per dimension, each grouped
by customer, so the number of round trips does not grow with the number of
customers. Every statement is parameterized, reads `transaction` and
`transaction_line` (accepted sales, ADR-0020), and the module contains no write and
no commit; `tests/test_consumption_shift_db.py` asserts each of those.

## Not evidenced here

A page, a filter or an export of these shifts (F12-03). Restricting a report to one
customer or segment is left to the caller.

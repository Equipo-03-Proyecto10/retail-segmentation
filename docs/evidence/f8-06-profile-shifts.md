# F8-06 — Before and now on the consumption profile, with shares and a claimable shift (#341)

Evidence that the consumption profile shows a customer's dominant channel and
store, and their top categories, in an earlier and a recent period with their
share of purchases; flags a shift only when RN-50 allows one; and says why when it
does not. The shift report on `/consumption-reports/` applies the same rule, shows
shares on its arrows, and states how its counts reconcile.

Covers the three acceptance criteria of #341, business rule RN-50 and the change
to RN-36. No schema change; everything here reads.

## How it was produced

A throwaway PostgreSQL 16 container loaded with the three scripts in order, the
application run locally against it as `retail_app`, signed in as the seeded
demonstration administrator, and captured with Playwright (Chromium) at 375 px and
1440 px, full page. No page overflowed the viewport horizontally; the before/now
table scrolls inside its own wrapper at 375 px, the pattern the rest of the
application uses.

The new statement — the fourth grouping of purchases with product lines and the
per-customer filter — ran as `retail_app` without error. On the seed alone, over
two 90-day periods, the report compares 30 customers and claims **0** shifts; **12**
are undecided, every one a category whose top two tied on purchases in a period.
Before RN-50 those 12 were reported as shifts, decided by spend.

The seed has no customer whose behaviour clearly changed, so eight recent
purchases through another channel, at another store and in another category were
added to *Demo Customer 1* in that throwaway database only.

## What each screenshot shows

| Screenshot | Acceptance criterion | What to look for |
|---|---|---|
| `f8-06-profile-shifted-{375,1440}.png` | 1, 2, 3 | *Demo Customer 1*, 4 purchases before and 14 now. Channel **marketplace 100 % (4 of 4) → mobile_app 57 % (8 of 14), Shifted**; store **Store 8 100 % → Store 1 57 %, Shifted**. Category **No shift claimed**: Snacks and Bakery tied 3–3 in the earlier period. Top categories before and now with their shares, the earlier ones adding up to 175 %, and the note that category shares can exceed 100 %. |
| `f8-06-profile-tie-no-shift-{375,1440}.png` | 2, 3 | *Demo Customer 9*, seed data unchanged: channel and store unchanged; the leading category moved from Small electronics to Dairy, but the later period is a 3–3 tie, so no shift is claimed and the reason is stated. |
| `f8-06-report-counts-shares-{375,1440}.png` | 1, 3 | The shift report over 90-day periods: *30 customers compared: 1 shifted, 29 without a shift (12 whose leader changed with too few purchases or a tie to claim it). 0 with sales in only one period.* The one shift, *Demo Customer 1*, shows **marketplace 100 % → mobile_app 57 %** and **Store 8 100 % → Store 1 57 %**, matching the profile. |

## Checks

- `pytest`: 2693 passed. `black --check .` and `ruff check .`: clean.
- RN-50's edges — three purchases against two, a 1-vs-1 tie, a tie broken by spend
  in each period, a category counted over purchases with product lines, shares over
  100 %, an odd and a one-day window — are in `tests/test_consumption_shift.py`; the
  statement in `tests/test_consumption_shift_db.py`; the profile page in
  `tests/test_consumption_profile_shifts_route.py`; the report page in
  `tests/test_consumption_reports_route.py`.
- Lowering the minimum to 2, ignoring ties, counting categories over every
  purchase, and claiming any changed leader each failed between 3 and 10 tests.

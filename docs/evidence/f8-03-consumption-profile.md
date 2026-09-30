# F8-03 — The customer consumption profile

Evidence that a customer can be understood without reading their transaction
list: one profile per customer, computed from accepted sales over a stated
window, carrying R, F and M and the current and previous segment from
assignment history.

Covers the five acceptance criteria on F8-03 and business rules RN-34 and
RN-35. The page that shows the profile is F8-04 and is not evidenced here.

## How this run was produced

Against the database the three ordered scripts build, from empty:

```bash
psql -v ON_ERROR_STOP=1 -f sql/00_create_database.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/01_schema.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/02_seed_30_per_table.sql
```

The run used PostgreSQL 16.2; `pg_trgm` was stripped from `00_create_database.sql`
locally because that local build does not provide it.

```
customers 30 · transactions 300 · transaction lines 600 · runs 30 · history rows 900
```

`build_profile` was then called from Python with the application's own code, and
every figure it returned was recomputed by a separately written `SELECT` and
compared. The application role `retail_app` — not the schema owner — was used
for the whole run.

## One customer's profile

Customer `00000000-0000-0000-0000-000000000001`, default 180-day window:

```
window          : 2026-03-31 -> 2026-09-27 (180 days)
total_spend     : 1420.00
purchase_count  : 10
average_ticket  : 142.00
last_purchase   : 2026-09-26
dominant channel: marketplace (10 purchases, 1420.00)
dominant store  : Store 8 (10 purchases, 1420.00)
fav categories  : Snacks (6 purchases, 18 units) · Bakery (6, 12) · Sweet bread (2, 6)
frequent prods  : Demo Product 3 (3, 9) · Demo Product 33 (3, 9) · Demo Product 2 (3, 6)
                  · Demo Product 32 (3, 6) · Demo Product 13 (2, 6)
avg discount %  : 64.94
rfm             : F=11 · M=402.50 · scores=2/1/4 (run 30, 180 days)
current segment : LOYAL     (run 30, open)
previous segment: CHAMPION  (run 29, before the current LOYAL streak)
```

The two ties in the product list are real and were broken by the stated rule:
products 3 and 33 tie on 3 purchases and 9 units, and product 3 comes first on the
lower id; the same for 2 and 32.

Cross-check, each side written independently:

| Figure | Profile | Independent `SELECT` |
|---|---|---|
| purchases, spend, last purchase | 10 · 1420.00 · 2026-09-26 | same |
| average ticket | 142.00 | `1420.00 / 10` |
| dominant channel, dominant store | 4 · 8 | first row of `ORDER BY count DESC, sum DESC, id ASC` |
| average discount | 64.94 | `1 - Σ(qty·unit_price)/Σ(qty·list_price)`, ×100 |
| current and previous segment | run 30 LOYAL · run 29 CHAMPION | current label streak · first prior different label |

```
All cross-checks against independent SQL agree.
```

## The acceptance criteria, one by one

Records created for the scenarios were inside transactions that were rolled
back, so the seed was left as it was found.

**Every measure, and R/F/M with both segments.** The block above. The R, F and M
values and scores are the open history row's, with the run that measured them and
its window carried alongside (run 30, 180 days).

**No accepted sales → absent sales measures, not an error and not zeros.**
Scenario B inserted a customer with neither sales nor assignment history:

```
has_sales=False  total_spend=None  average_ticket=None  purchase_count=None
last_purchase_at=None  dominant_channel=None  dominant_store=None
favourite_categories=()  frequent_products=()  average_discount_pct=None
rfm=None  current_segment=None  previous_segment=None
```

The assignment-history read still runs; only the remaining sales reads are
skipped. An empty sales profile now keeps assignment history when it exists.

**One assignment only → the previous segment is absent.** A customer with a sale and
a single open history row:

```
current : CHAMPION | previous: None
```

**A tie is broken deterministically, and the rule is stated.** A customer with two
channels and two stores, each tied at one purchase and 100.00, the higher ids
inserted first:

```
channels tied at 1 purchase / 100.00 each -> dominant channel id: 1
stores   tied at 1 purchase / 100.00 each -> dominant store id  : 1
repeated 5x -> same answer
```

The lowest id wins whatever order the rows arrive in; the unit tests confirm the
same for every permutation of the input. The rules are in RN-35 and at the top of
`web/services/consumption_profile.py`.

**Unassigned is not absent.** A previous or current result of *unassigned* (RN-21)
is a state a run recorded, and stays distinct from having no assignment at all:

```
current : None (unassigned)  | rfm: None
previous: CHAMPION
```

The point is only that the two absences are told apart.

**Unchanged reruns do not reset the current segment's `since`.** The history
read groups contiguous rows with the same label (including consecutive
unassigned rows), reports the open row's latest R/F/M values, and uses the
streak's first `valid_from` as the current segment start. Its previous segment
is the first row before that streak whose label differs. The focused regression
tests cover the repeated-label case and the SQL boundary that treats NULL as a
real unassigned state.

These definitions and the previous-segment semantics were accepted by the
Proxy PO in issue #211 on 2026-09-27, including the meanings of discount,
spend and an empty sales profile.

## Additional boundary scenarios

**Lapsed customer.** Scenario F gave a customer a sale outside the profile
window and an open assignment with stored R/F/M. The sales-derived values are
absent, while assignment history remains:

```
has_sales: False
total_spend: None
purchase_count: None
rfm: run 30 · F=4 · M=321.00 · scores=4/3/2
current segment: CHAMPION (run 30)
```

**Unknown customer.** Scenario G passed one well-formed UUID that names no
customer and one malformed id. Both raised `UnknownCustomer`, which the F8-04 page
will map to HTTP 404.

**The same history row is never both current and previous.** Scenario H built
all 30 seeded profiles and found zero repeated rows. The current label streak
and its preceding different row come from one `get_current_and_previous_history_rows`
statement, so a segment run committed between two `READ COMMITTED` reads cannot
create that contradiction.

## What the seed does to the numbers

Properties of the seed, found while cross-checking, that a reader of this
evidence should know before reading the figures above.

**Every `transaction.total` equals the sum of its lines in the seed.** The
reconciliation found zero mismatches across 300 transactions. PR #256 made the
seed derive every header total from its lines, matching the CSV ingestion rule
in ADR-0020.

**The seeded history's raw R/F/M values are illustrative.** The seed says so
itself. For this customer the stored open row holds frequency 11 and monetary
402.50, while the profile window contains 10 purchases and 1420.00 spend:

```
seeded history says F/M   : 11 402.50 (illustrative seed values)
profile purchases / spend : 10 1420.00
```

**The discount is a comparison with today's list price.** RN-13 stores what was
charged, not the list price a sale was made against, so the figure moves when the
catalog price does. On the seed, priced independently of the catalog, the
per-line difference ranges from −286.7 % to +86.7 %, and 175 of 600 lines were charged more than today's
list price. That is why the profile reports the figure as measured and never
clamps it, and why RN-35 says what it is and is not.

## Quality gates

```
$ .venv/bin/pytest -q
2946 passed in 188.91s
$ .venv/bin/black --check .
191 files would be left unchanged.
$ .venv/bin/ruff check .
All checks passed!
```

ADR-0017's `git grep -n current_segment_id sql/01_schema.sql web/ tests/` now
prints nothing; the schema comment that used to match was reworded in this PR.

The new module reads `transaction`, `transaction_line`, `product`, `category`,
`store`, `channel`, `customer_segment_history`, `segmentation_run` and
`segment_label`, as the story lists, plus `customer` through the existing
`web.db.customers.get_customer` to refuse an unknown id. It writes nothing. It
never selects `segmentation_run.method`, which `tests/test_consumption_db.py`
asserts against the text of the history statement (ADR-0018).

With the restricted application role:

```
connected as: retail_app | superuser: False
```

## Not evidenced here

The server-rendered page captures (F8-04), shift detection (F8-05), and any use
of the profile by recommendations (F10-01).

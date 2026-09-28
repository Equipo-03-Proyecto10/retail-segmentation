# F8-03 — The customer consumption profile

Evidence that a customer can be understood without reading their transaction
list: one profile per customer, computed from accepted sales over a stated
window, carrying R, F and M and the current and previous segment from
assignment history.

Covers the five acceptance criteria on F8-03 and business rules RN-33 and
RN-34. The page that shows the profile is F8-04 and is not evidenced here.

## How this run was produced

Against the database the three ordered scripts build, from empty:

```bash
psql -v ON_ERROR_STOP=1 -f sql/00_create_database.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/01_schema.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/02_seed_30_per_table.sql
```

```
customers 30 · transactions 300 · transaction lines 600 · runs 30 · history rows 900
```

`build_profile` was then called from Python with the application's own code, and
every figure it returned was recomputed by a separately written `SELECT` and
compared. The application role `retail_app` — not the schema owner — was used
for the run recorded in the last section.

## One customer's profile

Customer `00000000-0000-0000-0000-000000000001`, default 180-day window:

```
window          : 2026-04-01 -> 2026-09-28 (180 days)
total_spend     : 2750.00
purchase_count  : 10
average_ticket  : 275.00
last_purchase   : 2026-09-27
dominant channel: marketplace (10 purchases, 2750.00)
dominant store  : Store 8 (10 purchases, 2750.00)
fav categories  : Snacks (6 purchases, 18 units) · Bakery (6, 12) · Sweet bread (2, 6)
frequent prods  : Demo Product 3 (3, 9) · Demo Product 33 (3, 9) · Demo Product 2 (3, 6)
                  · Demo Product 32 (3, 6) · Demo Product 13 (2, 6)
avg discount %  : 64.94
current segment : LOYAL     (run 30, open)
previous segment: CHAMPION  (run 29, closed when run 30 opened)
```

The two ties in the product list are real and were broken by the stated rule:
products 3 and 33 tie on 3 purchases and 9 units, and product 3 comes first on the
lower id; the same for 2 and 32.

Cross-check, each side written independently:

| Figure | Profile | Independent `SELECT` |
|---|---|---|
| purchases, spend, last purchase | 10 · 2750.00 · 2026-09-27 | same |
| average ticket | 275.00 | `2750.00 / 10` |
| dominant channel, dominant store | 4 · 8 | first row of `ORDER BY count DESC, sum DESC, id ASC` |
| average discount | 64.94 | `1 - Σ(qty·unit_price)/Σ(qty·list_price)`, ×100 |
| current and previous segment | run 30 LOYAL · run 29 CHAMPION | open row · last closed row by `valid_to` |

```
All cross-checks against independent SQL agree.
```

## The acceptance criteria, one by one

Scenarios B to E build their customers inside a transaction that is rolled back,
so the seed is left as it was found.

**Every measure, and R/F/M with both segments.** The block above. The R, F and M
values and scores are the open history row's, with the run that measured them and
its window carried alongside (run 30, 180 days).

**No accepted sales → an empty profile, not an error and not zeros.** A customer
inserted with no sale:

```
has_sales=False  total_spend=None  average_ticket=None  purchase_count=None
last_purchase_at=None  dominant_channel=None  dominant_store=None
favourite_categories=()  frequent_products=()  average_discount_pct=None
rfm=None  current_segment=None  previous_segment=None
```

Nothing is queried after the first statement, which `tests/test_consumption_profile.py`
asserts by giving every other read a fake and requiring it was never called.

**One assignment only → the previous segment is absent.** A customer with a sale and
a single open history row:

```
current : CHAMPION | previous: None
```

**A tie is broken deterministically, and the rule is stated.** A customer with two
channels and two stores, each tied at one purchase and 100.00, the higher ids
inserted first:

```
channels tied at 1 purchase / 100.00 each -> dominant channel id: 2
stores   tied at 1 purchase / 100.00 each -> dominant store id  : 10
repeated 5x -> same answer
```

The lowest id wins whatever order the rows arrive in; the unit tests confirm the
same for every permutation of the input. The rules are in RN-34 and at the top of
`web/services/consumption_profile.py`.

**Unassigned is not absent.** A previous or current result of *unassigned* (RN-21)
is a state a run recorded, and stays distinct from having no assignment at all:

```
current : None (unassigned)  | rfm: None
previous: CHAMPION run 30
```

That customer was constructed for the case, so its run numbers are not
chronological; the point is only that the two absences are told apart.

## What the seed does to the numbers

Two properties of the seed, found while cross-checking, that a reader of this
evidence should know before reading the figures above.

**`transaction.total` never equals the sum of its lines in the seed.** Zero of 300
transactions match. Sales that arrive through the CSV do: ADR-0020 derives the
total from the lines. The profile takes spend from the header total because that
is what the segment run scores as Monetary, so a profile's spend and its M value
are one measurement. Correcting the seed is not part of this story.

**The seeded history's raw R/F/M values are illustrative.** The seed says so
itself. For this customer the stored open row holds frequency 11 and monetary
402.50, while the sales say 10 and 2750.00. After a real segment run they agree:

```
seeded history says F/M   : 11 402.50 (illustrative seed values)
profile purchases / spend : 10 2750.00
stored   F / M after run  : 10 2750.00
```

**The discount is a comparison with today's list price.** RN-13 stores what was
charged, not the list price a sale was made against, so the figure moves when the
catalog price does. On the seed, priced independently of the catalog, the
per-line difference ranges from −286.7 % to +86.7 %, and 175 of 600 lines were
charged more than today's list price. That is why the profile reports the figure
as measured and never clamps it, and why RN-34 says what it is and is not.

## Quality gates

```
$ pytest -q
960 passed          # 895 before this story, plus 65 in
                    # tests/test_consumption_profile.py and tests/test_consumption_db.py
$ black --check .   # 86 files would be left unchanged
$ ruff check .      # All checks passed!
```

ADR-0021's compliance check, run as written, prints nothing: no
scientific-computing dependency was added, and `web/requirements.txt` is unchanged.

ADR-0017's `grep -rn current_segment_id sql/01_schema.sql web/ tests/` prints one
line, `sql/01_schema.sql:478`, a comment that already named the retired column
before this story. Nothing this story added references it. It is recorded here
because the ADR says the command must print nothing.

The new module reads `transaction`, `transaction_line`, `product`, `category`,
`store`, `channel`, `customer_segment_history`, `segmentation_run` and
`segment_label`, as the story lists, and writes nothing. It never selects
`segmentation_run.method`, which `tests/test_consumption_db.py` asserts against
the text of both history statements (ADR-0018).

With the restricted application role:

```
connected as: retail_app | superuser: False
profile ok  : True 10 2750.00 LOYAL <- CHAMPION
```

## Not evidenced here

The server-rendered page (F8-04, with its 375 px and 1440 px captures), shift
detection (F8-05), and any use of the profile by recommendations (F10-01). The
profile has no route yet, so there is no screen to capture for this story.

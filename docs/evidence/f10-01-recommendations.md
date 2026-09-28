# F10-01 — Product recommendations with a stated reason for each

Evidence that a recommendation can be defended to the business and acted on in a real
store: every recommended product has stock in the customer's usual store, matches at
least one stated signal, and carries the reasons it was chosen; and when there is
nothing to recommend from, the result says so instead of falling back to something else.

Covers the six acceptance criteria on F10-01 and business rule RN-40. It is a computation
and has no screen: the view, with its permission, is F10-02. No schema change, no new
dependency, no new permission, and nothing here writes.

## What this delivers

| | Where |
|---|---|
| The computation: eligibility, three signals, ranking, reasons, and the two ways it declines to recommend | `web/services/recommendations.py` |
| Two reads: what a store has in stock, and what a segment's customers bought | `web/db/recommendations.py` |

The open assignment, the usual store and the categories the customer buys from are **not**
re-derived: they come from the consumption profile (F8-03), so that "usual store" is the
profile's dominant store, as the story's note says, and means the same thing on both
surfaces.

## The decisions, made explicit

The story fixes what a recommendation must combine and what it may not do. It does not
fix how the pieces combine, so these are decisions, written into RN-40 and pinned by
tests, for the Proxy PO to confirm:

| Question | Decision |
|---|---|
| What is eligible? | Active, a **positive** quantity on hand in the usual store, and not already bought by the customer in the window |
| What makes a product relevant? | At least one of three signals: other customers in the same segment bought it, it is in a preferred category, or it is in a category the customer buys from. *In stock* is not a reason |
| How are they ordered? | Number of signals matched, then segment buyers, then the category's share of the customer's buying, then product id. **No weights** |
| A customer with no open assignment | The result says no segment is available. It reads nothing else |
| A customer whose latest assignment is *unassigned* | The same, and says which run left them so (RN-21) |
| A customer with no sale in the window | No usual store, so stock cannot be checked: the result says so and recommends nothing |
| Which is checked first? | The segment |

## How this run was produced

Against the database the three ordered scripts build from empty, after one real
`RFM_RULES` run (run 31: 30 customers assigned), with the application's own code. The seed
has 150 inventory rows across 30 stores, **none at zero stock and none for an inactive
product**, so those cases were made in transactions that were rolled back.

## Criterion 1 — the label, the preferred categories and the history

One customer, in full (window 2026-04-01 to 2026-09-28):

```
Demo Customer 1: status=recommended segment=Lost usual store=Store 8
3 recommendations from what Store 8 has in stock.
 1. Demo Product 4 (product 4, Beverages) — 22 in stock
      [segment] 2 other Lost customers bought it in the last 180 days
      [preferred_category] In Beverages, a category the customer said they like
 2. Demo Product 1 (product 1, Dairy) — 19 in stock
      [segment] 3 other Lost customers bought it in the last 180 days
 3. Demo Product 5 (product 5, Household cleaning) — 23 in stock
      [segment] 1 other Lost customer bought it in the last 180 days
```

Product 4 matches two signals and ranks above the two that match one. Each reason states
the figure behind it (2 other customers, 22 in stock), so a person can check it.

**All 30 seeded customers were compared with an independently written query** that
recomputes the customer's segment, usual store, the products they already bought, their
top categories, their interests, the segment's buyers and the ranking on its own:

```
statuses: {'recommended': 30}
customers whose recommended products, order and signal counts equal the SQL: 30 of 30 (107 recommendations compared)
```

## Criteria 2 and 3 — only what the usual store has in stock

For the customer above, whose top recommendation is product 4 with 22 in stock at store 8:

```
  stock set to 0 in that store -> still recommended? False
  ...and 500 in store 1, a store that is not theirs -> recommended? False
  product deactivated -> recommended? False
```

A product with no stock never appears, one that only another store holds never appears,
and an inactive product never appears. The quantity is checked twice, in the read
(`quantity_on_hand > 0`) and again in the ranking, because stock is the one thing a
recommendation must never get wrong.

## Criterion 4 — every recommendation carries its reason

Every recommendation above lists the signals that matched, in a fixed order (segment,
preferred category, purchase history), in words and with the figures. The tests require
at least one reason on every recommendation, and the counts in a reason to be the counts
the signal was computed from (*1 other Lost customer*, *6 of their 10 purchases*).

## Criterion 5 — no segment is stated, and nothing is substituted

```
Demo Customer 10 with no assignment at all -> no_segment: No segment is available for this customer: no segmentation run has assigned them one, so no recommendations are given.
Demo Customer 1, left unassigned by the latest run -> no_segment: No segment is available for this customer: run 32 left them unassigned, because they had no sales in its window, so no recommendations are given.
```

The two cases read differently, because they mean different things: never scored, and
scored and found nothing to label. In both, the stock, what other customers bought and the
customer's history are **not read at all**; a test asserts that none of them is called.

The same honesty applies to the other thing a recommendation needs. A customer who has a
segment but no accepted sale in the window has no usual store:

```
Demo Customer 11 -> no_usual_store: No usual store is available: the customer has no accepted sale in the last 1 day, so stock cannot be checked and no recommendations are given.
```

## Criterion 6 — only the stable label is read

Three checks, each different:

* **The text.** The two modules that compute a recommendation contain neither the word
  for how a run is produced nor any cluster vocabulary, and a test reads them to prove it.
  The signature asks for a customer and a window, and nothing else.
* **The statements.** Every statement one recommendation runs was captured on the real
  database (12 for one customer):

  ```
  any mention of `method`: False | of `cluster`: False
  tables read: category, customer, customer_interest_category, customer_segment_history,
    inventory, product, segment_label, segmentation_run, store, transaction, transaction_line
  writes: False
  ```

  `segmentation_run` is read by the consumption profile, only for a run's date and window
  (F8-03); nothing reads its method.
* **The behaviour.** The open assignments of all 30 customers were replaced by a run of the
  other kind that carries *exactly the same labels*. The recommendations for every customer
  were then computed again:

  ```
  the open assignments now come from a run of kind: [('KMEANS',)]
  recommendations for all 30 customers identical before and after: True
  ```

  Products, order and reasons are identical, because there is nothing but the label to
  read. That run also has no `segment_id`, so a K-means-labelled customer is recommended
  to exactly like any other (#272 does not touch this).

## The tests catch a broken recommendation

Faults were seeded into the code one at a time, and each was required to make a test fail:

| Fault seeded | Tests that failed |
|---|---|
| Zero stock allowed (in the ranking) | 2 |
| Zero stock allowed (in the read: `>= 0`) | 1 |
| Stock read for any store | 1 |
| Inactive products offered | 1 |
| Products the customer already bought recommended | 2 |
| A product with no signal recommended | 2 |
| Fewer signals ranked first | 1 |
| Category share ranked before segment buyers | 1 |
| Ties broken by the higher product id | 2 |
| The limit ignored | 1 |
| The segment taken as everyone who ever held the label | 1 |
| The customer counted in their own segment | 1 |
| Buyers counted per purchase, not per person | 1 |
| A customer with no assignment falls through to a recommendation | 4 |
| An unassigned latest assignment falls through | 1 |
| No usual store: recommends anyway | 1 |
| Stock read for the wrong store | 1 |

One fault **survived the first time**: replacing the store filter with a condition that
ignores the store broke no test, because the test only checked that the store *parameter*
appeared in the statement and a statement can carry a parameter and ignore it. That is
criterion 2, so it was closed with a test that pins the filter clause itself, and the fault
now fails one.

A second was found in review: ranking by category share before segment buyers broke no
test, because the test named for that order only varied the buyers. RN-40 puts buyers
first, so a test now compares the two directly, and the fault fails it.

## Tests

`tests/test_recommendations.py` (42) and `tests/test_recommendations_db.py` (16) are new.

```
$ pytest -q
1369 passed         # 1311 on develop, plus 58
$ black --check .   # All done
$ ruff check .      # All checks passed!
```

## Things to know

* **The story's data-model list names `customer_preferred_channel`.** No criterion and no
  note uses channels, so it is not read. If a recommendation should depend on the customer's
  preferred channel, that needs a criterion.
* **"Already bought" means within the window.** A product bought longer ago than the profile
  window (180 days by default) can be recommended again.
* **Categories are matched as the product carries them,** with no roll-up to a parent, as
  the consumption profile does. A customer whose registered interest is a *parent* category
  matches only products in that exact category.
* **A segment of one gives no segment signal.** With no other customer in the segment there
  is nobody whose purchases can speak for it, so the other two signals decide.
* **The seed has no zero-stock rows and no inactive products,** so criterion 3 was shown by
  making those states inside rolled-back transactions.

## Not evidenced here

A page or a permission (F10-02, which this story blocks), and the filtered recommendation
reports built on it (F12-03).

# F24-01 (#824) — today's recommendation guarantee, tested

The starting point of Phase 24, recorded before the rule changes. Governing
decisions: RN-40, E-06 (the stock figure an item carries), E-07 (seven
published views).

## What was run

`services/recommendations/tests/test_guarantee.py`, 14 tests, against the seeded
database of the three scripts (PostgreSQL 17 in Docker on the Pi, since no local
server exists; CI runs PostgreSQL 18). Each test builds a throwaway scenario in
one transaction as the owner (stores 9001 and 9002, category 9001, products
900001 to 900009, two customers in the `LOYAL` segment), reads it as
`svc_recommendations` through the seven views, and rolls back.

```
cd services/recommendations
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/retail \
MOSAIQ_REQUIRE_POSTGRES=1 pytest tests/test_guarantee.py
14 passed
```

Mutation check: removing `p.is_active` from `store.stocked_products` and the
already-bought filter from `rule.rank` made four of the tests fail
(inactive, bought, the positive-half check, alternative); they pass restored.

## The guarantee, by acceptance criterion

| Criterion | Scenario | Result |
|---|---|---|
| Zero stock at the usual store | Matching product with quantity 0 at the usual store (10 elsewhere), and one with no inventory row | Neither appears |
| Held only elsewhere | Matching product stocked only at another store | Does not appear |
| Inactive | Matching, stocked, `is_active = false` | Does not appear |
| Bought in the window | Matching, stocked, bought 10 days before the as-of moment | Does not appear; the same product bought 400 days before does (the window is the boundary) |
| Reasons | The one product left, with a segment peer who bought it | Three reasons in the fixed order, each naming a signal and its figures: `segment` with `segment_buyers = 1` (checked against the owner's tables), `preferred_category` with the category id, `purchase_history` with the category id and `share = 1` |

Every item returned was also checked against the owner's tables: active,
positive stock at the usual store, and `quantity_on_hand` equal to the row. A
product in stock that matches no signal is not offered ("in stock is not a
reason").

## What the current rule cannot do

Pinned by the last four tests, so F24 changes them on purpose.

- **Choose a store.** The store is derived from the customer's purchases in the
  window (the dominant store, RN-35). The caller cannot name one and the answer
  carries no store per item. A product held at another store is never
  considered, however close, or however empty the usual store is.
- **Consider a channel.** `Line` carries no channel; a purchase's channel
  changes nothing (the test moves every purchase to another channel and the
  answer is identical). A customer who buys online and a customer who buys in
  store are treated alike.
- **Offer an alternative.** When the only matching product has no stock at the
  usual store the answer is `none_match` with no items. Nothing takes its place
  and nothing says what was lost. The same gap covers a discontinued product:
  there is no such state, only `is_active`.
- **Keep the explanation.** The reasons exist only in the response. No table
  in any schema holds a recommendation (E-07), and the cache holds a result only
  until a stream event empties it. Asking again recomputes; the explanation
  given yesterday cannot be shown today.

## Not covered

The HTTP path and the cache (Redis) are covered by the existing suites and were
not run here; the 59 tests that need Redis were skipped locally. The whole
service suite otherwise passed (81 passed).

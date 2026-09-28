# F10-02 — A customer's recommendations, with the reason for each

Evidence that staff can open a customer and read what to offer them, why, and whether it
is on the shelf: `/catalog/customers/<id>/recommendations` shows each recommended product
with the store, the stock available and the reason it was chosen, is computed afresh on
every load so a product whose stock reaches zero is gone on the next reload, tells a
customer with nothing to recommend why instead of showing an empty table, and is refused
by the default-deny gate to any profile without `segment.read`.

The recommendations are computed by F10-01 (evidenced in
[`f10-01-recommendations.md`](f10-01-recommendations.md)); this story adds the page. No
schema change, no new permission, no new dependency, and nothing here writes.

## What this delivers

| | Where |
|---|---|
| The page and its route, gated on `segment.read` | `customer_recommendations` in `web/routes/catalog.py`, `web/templates/catalog/customer_recommendations.html` |
| Links to it from the customer page and the consumption profile | `customer_detail.html`, `customer_profile.html` |
| The customer's name on the result, so the page needs no second query | `RecommendationResult.customer_name` in `web/services/recommendations.py` (F10-01) |

## Open point: the role the story names is refused

The story is written *as a store manager*, and `STORE_MANAGER` holds no `segment.read`, so
that role is turned away (captured below). This is not an oversight in the permission map.
`docs/requirements.md` and ADR-0010 keep customer data behind `segment.read` **so that
`STORE_MANAGER` and `INVENTORY_PLANNER` stay out of the customer directory**, and the story's
own note says to gate the page through the permission F4-07 assigns, which is `segment.read`.
The requirement was followed as written.

Making the page reachable to a store manager means either granting them `segment.read`,
which opens the whole customer directory, segment membership and every other `segment.read`
surface to them and reverses ADR-0010, or giving this page a different permission and
deciding what a store manager may see about a customer, for example only customers of their
own store. That is a decision about the permission matrix and about customer data, so it is
not made here. It is the same question raised on the consumption profile (F8-04).

## How this run was produced

Against the database the three ordered scripts build from empty, with the application run as
the restricted role `retail_app` and the page opened in headless Chromium, signed in as
seeded demonstration accounts (`user14@…` ANALYST, `user15@…` STORE_MANAGER), after one real
`RFM_RULES` run. Nothing is credential-bearing in any capture.

## A customer with recommendations

| | |
|---|---|
| 1440 px | [`f10-02-recommendations-1440.png`](f10-02-recommendations-1440.png) |
| 375 px | [`f10-02-recommendations-375.png`](f10-02-recommendations-375.png) |

*Demo Customer 1* has three recommendations from what Store 8 stocks. Each one is a card
that shows, in its own markup and not merely somewhere on the page:

| | Shown as |
|---|---|
| The product | its name, linked to its page, and its category |
| The store | `Store 8` |
| The available stock | `22 units` (a single unit reads `1 unit`) |
| The reason | each signal that matched, named (*Segment*, *Preferred category*, *Purchase history*) and in words with its figures, for example *2 other Lost customers bought it in the last 180 days* |

Above the cards, *Based on* states the segment, the usual store and the window the list was
computed over. A card, and not a table row, because at 375 px the reason has to sit next to
the product without scrolling sideways to find it. The window control comes after the
results, since it is secondary at a counter.

## Stock that reaches zero

This was done for real, against the running application, between two loads of the same page:

```
before: 3 recommendations; first is product 4, 22 in stock at Store 8 (page says: 22 units)
after stock reached zero and the page was reloaded: 2 recommendations; product 4 still listed? False
stock restored (22) and reloaded: product 4 back? True
```

The database and the page agreed on the figure (22), the product left the list when its stock
in that store reached zero, and returned when it was restored. Capture after the reload:
[`f10-02-stock-zero-reloaded-1440.png`](f10-02-stock-zero-reloaded-1440.png).

Two things make that reliable. Nothing is kept between requests, and a test loads the page
twice with the stock changing between the loads. And the response is sent
`Cache-Control: no-store`, so a browser does not show an older list on reload; it was present
on every response captured.

## When there is nothing to recommend, the page says why

| Situation | Title | Captures |
|---|---|---|
| Nothing in the usual store matches the customer (here, the store's whole stock was set to zero) | *No eligible products* | [1440](f10-02-no-eligible-1440.png) · [375](f10-02-no-eligible-375.png) |
| No run has assigned the customer a segment | *No segment available* | [1440](f10-02-no-segment-1440.png) · [375](f10-02-no-segment-375.png) |
| A segment, but no accepted sale in the window, so no usual store (window of 1 day) | *No usual store* | [1440](f10-02-no-usual-store-1440.png) |

Each shows the explanation F10-01 computed, for example *No usual store is available: the
customer has no accepted sale in the last 1 day, so stock cannot be checked and no
recommendations are given.* In every case no card and no table is rendered, and a test
asserts it.

## A profile without the permission is refused

| Signed in as | Result |
|---|---|
| ADMIN, ANALYST, MARKETING, AUDITOR | 200 |
| STORE_MANAGER, INVENTORY_PLANNER, CUSTOMER | **403** |
| nobody | 302 to sign-in |

Captured for a store manager:
[`f10-02-refused-store-manager-1440.png`](f10-02-refused-store-manager-1440.png). The route is
in the negative-flow matrix, so the anonymous and per-role refusals run for it too, and a
`POST` answers 405. See the open point above.

## 375 px and 1440 px

Horizontal overflow of the page body, measured in the browser as `scrollWidth - clientWidth`,
was **0 px** for every capture at both widths. At 375 px the cards stack into one column and
each keeps its store, stock and reasons together.

## Other behaviour

* **The window** is read with the parser the segment run uses (default 180 days). One it would
  refuse answers **400** and explains itself, and no recommendation is computed:
  [`f10-02-invalid-window-1440.png`](f10-02-invalid-window-1440.png).
* **An unknown customer** answers 404, whether the window is valid or not.
* **The page never names how a run was produced,** nor any cluster: no page text contains
  `rfm_rules`, `kmeans`, `k-means` or `cluster` (ADR-0018).
* **Names are escaped:** a customer or product name holding markup is shown as text.

## The tests catch a broken page

Faults were seeded into the route and the template, one at a time, and each was required to
make a test fail. All did:

| Fault seeded | Tests that failed |
|---|---|
| The permission relaxed to `catalog.read` | 2 |
| The response left cacheable | 1 |
| The window parameter ignored | 2 |
| An unknown customer not answering 404 | 1 |
| An invalid window not refused | 7 |
| The store missing from each card | 1 |
| The stock missing | 2 |
| The reasons missing | 1 |
| Stock or reasons shown on only the first card | 1 each |
| The empty state removed (an empty grid instead) | 3 |
| The product not linked | 1 |
| A single unit written in the plural | 1 |

One fault **survived the first time**. The store missing from each card broke nothing,
because the test counted `Store 8` across the *whole page*, where the header and the *Based
on* panel already say it. The criterion is that *each recommendation* shows its store, so the
test now reads each recommendation's own card, and the same fault now fails.

## Tests

`tests/test_customer_recommendations_route.py` (33) is new, one test was added to
`tests/test_recommendations.py` for the customer's name, and the negative-flow matrix now
includes the route.

```
$ pytest -q
1405 passed         # 1369 on develop, plus 36
$ black --check .   # All done
$ ruff check .      # All checks passed!
```

## Things to know

* **Both `Consumption profile` and `Recommendations` are reachable from the customer page,**
  and each links to the other.
* **The list is for the customer's usual store only,** as F10-01 decides. A customer who
  shops in several stores sees what their dominant one has in stock.
* **The page reads live stock on every load,** which is what the story asks, and means a busy
  counter runs the recommendation query each time.

## Not evidenced here

Filtered recommendation reports (F12-03), and any per-store scoping of what a store manager
may see, which is the open point above.

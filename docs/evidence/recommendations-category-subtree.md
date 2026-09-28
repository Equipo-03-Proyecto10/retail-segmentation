# Recommendations: a category covers the categories below it

Evidence for #277, a change to F10-01 (#218) decided in the review of #276. A preferred
or bought category now matches a product in that category or in any category below it,
at any depth, and never one above or beside it. Before, categories were matched as the
product carries them, so an interest in *Beverages* never matched a product in *Soft
drinks*. RN-40 records the rule.

No schema change, no new configuration, and nothing written. The one new read is the
category hierarchy, through the existing `list_all_categories`.

## What changed

| | Before | After |
|---|---|---|
| Preferred category | The product's own category is an interest | The product's category, or any category above it, is an interest |
| Purchase history | The product's own category is among the top three bought (RN-35) | The same, or any category above it |
| Reason, matched through a category above | — | Names both: *In Soft drinks, part of Beverages, a category the customer said they like* |
| Several categories above match | — | The nearest is named, and its share is the one counted and ranked |

An exact match reads as it always did.

## How this run was produced

PostgreSQL 16.2 loaded from empty with the three scripts, as in
[`catalog-label-reads.md`](catalog-label-reads.md): the local build has no `pg_trgm`,
so that one line was left out of `00`. One `RFM_RULES` run (run 31, 30 customers) was
made through the application's code, and every seeded customer's recommendations were
computed as `retail_app`, with the limit at its maximum of 50, from `develop` at
`e8aea9c` and from this branch.

## On the seed, nothing changes, and the rule is checked independently

```
statuses before / after:                 recommended 30 / recommended 30
recommendations before / after:          107 / 107, none lost
customers whose recommendations changed: 0 of 30
preferred-category matches: app 7, independent SQL 7, equal: True
of which reached only through a category above the product's: 0
```

The independent query was written from the rule and not from the code. A recursive CTE
walks down from each customer's interests, and the query keeps the products the usual
store stocks, active and not bought in the window. It finds exactly the products the
application marks as a preferred-category match.

Nothing changes on the seed because its stock gives the rule nothing to act on. Every
store holds products 1 to 5 and nothing else, and all five sit in top-level categories.
No product in a subcategory is ever in stock.

## A product in a subcategory, in a rolled-back transaction

To show the difference on real rows, product 17 (*Soft drinks*, under *Beverages*) was
stocked at Demo Customer 1's usual store inside a transaction that was then rolled back.
Demo Customer 1 registered an interest in *Beverages* in the seed:

```
== develop
Demo Customer 1: interests Beverages, Snacks; usual store Store 8
Demo Product 17 is in Soft drinks, under Beverages; 20 stocked at Store 8 (rolled back)
  recommended: True
    [segment] 6 other Lost customers bought it in the last 180 days

== this branch
Demo Customer 1: interests Beverages, Snacks; usual store Store 8
Demo Product 17 is in Soft drinks, under Beverages; 20 stocked at Store 8 (rolled back)
  recommended: True
    [segment] 6 other Lost customers bought it in the last 180 days
    [preferred_category] In Soft drinks, part of Beverages, a category the customer said they like
```

After the rollback, `inventory` holds no row for product 17.

## Tests

In `tests/test_recommendations.py`:

* a preferred category covers the categories below it at any depth, and its reason names both;
* a category does not cover the one above it or one beside it;
* a category bought from covers the categories below it;
* the nearest matching category is the one named and counted, and its share is what ranks;
* an exact match reads as it always did;
* `recommend` reads the hierarchy, and a product under an interest is recommended;
* a customer with no segment still reads nothing else, the hierarchy included.

With the walk up the hierarchy removed, five of these fail. Taking the farthest matching
category instead of the nearest fails two.

```
$ pytest -q
1376 passed
$ black --check .   # 112 files would be left unchanged
$ ruff check .      # All checks passed!
```

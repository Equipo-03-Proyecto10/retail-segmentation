# ADR-0084 — A paused or discontinued product is not sellable, the elsewhere store is the nearest by great-circle distance, and a floor role's store is refused before it is looked up

**Status:** Proposed
**Owner:** Maximiliano
**Issue:** #827
**Supersedes:** in part [ADR-0067](0067-a-recommendation-is-judged-at-a-reference-store-and-channel-and-its-explanation-is-recorded.md) (the `inactive` row of the exclusion-reason table, "the product's `active` flag is false")
**Superseded by:** —

---

## Context

ADR-0067 lists `inactive` ("the product's `active` flag is false") before
`not_sellable` ("its commercial status does not allow the sale, such as
discontinued"), and allows an alternative for `not_sellable` but never for
`inactive`. When it was written, F21-09 had not yet published the commercial
status. It now has, and products keeps the two facts in step:
`public.product.is_active` is true exactly when the commercial status is
`active`, and `paused` and `discontinued` both set it false (products contract
1.2.0, decision 2; `api_products.product_ref.v1.md`). `api_stock.availability_v1`
reads that one column and calls it `NOT_SELLABLE`.

Read literally, ADR-0067 would therefore exclude every paused or discontinued
product as `inactive`, the first reason that applies, and `not_sellable` could
never fire. A discontinued product, the very case ADR-0067 names for an
alternative, could never get one.

Two more points were left open by ADR-0067 and the 2.0.1 contract, and #827 and
#830 cannot be built without them: what "nearest" means for an `elsewhere` store,
and which of 403 `forbidden_store` and 422 `unknown_store` a floor role gets for
a store id that does not exist.

## Decision

A product whose commercial status is not `active`, as `is_active` false
publishes it, is excluded as **`not_sellable`** and may be answered by an
alternative; `inactive` stays in the vocabulary, reserved for an `active` flag
that differs from the commercial status, and is not raised while products
publishes none. The **`elsewhere` store** is the nearest by great-circle
(haversine) distance between the reference store's coordinates and the other
store's, from `api_stores.store_directory_v2`; a tie goes to the lowest
`store_id`; a store without coordinates is never a candidate, and a reference
store without coordinates gets no `elsewhere` entry. For a **floor role** that
names a `store_id`, 403 `forbidden_store` is checked before 422 `unknown_store`
(then `unknown_channel`, then `unknown_customer`), as sales' `scope.py` does: a
store that does not exist is one the caller is not assigned to.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep `inactive` for `is_active` false, as ADR-0067's table reads | `not_sellable` would never fire, and a discontinued product would never get an alternative, which is the case ADR-0067 was written for |
| Map `paused` to `inactive` and `discontinued` to `not_sellable` | Neither the products view nor the availability view publishes the status, only `is_active`; recommendations would need a new grant on a products table or a new view, for a split no story asks for |
| Nearest by straight-line distance on raw latitude and longitude | A degree of longitude shrinks with latitude, so the order would be wrong between stores at different latitudes; haversine costs nothing more |
| Treat a store without coordinates as infinitely far but still eligible | It would be chosen whenever it is the only other store, and "nearest" would then name a store whose distance nobody knows |
| 422 `unknown_store` before 403 for a floor role | It tells a floor user which store ids exist, and sales already answers 403 first; two services would disagree on the same request |

## Consequences

**What this makes easy.** The exclusion reason follows the one fact products
publishes, the same one stock's availability view reads, so recommendations and
`GET /v1/availabilities` agree on what is not sellable. The `elsewhere` choice is
deterministic and testable from the seed's coordinates.

**What this makes hard.** `inactive` is a reason no answer carries today; a
reader of the decision record will never see it until products publishes a
separate flag, and that change needs its own record. Haversine treats the earth
as a sphere, which is close enough between stores but is not a road distance: the
nearest store may not be the quickest to reach.

**What must now be true elsewhere.**
- `contracts/recommendations/openapi.yaml` (2.0.2) states the three readings in
  `getRecommendationsV2`, and `contracts/recommendations/README.md` points here.
- recommendations-service (#827, #830) reads `api_stores.store_directory_v2`,
  `api_sales.channel_v1`, `api_users.store_assignment_v1` and
  `api_models.run_version_v1`, each granted in its boundary of
  `sql/01_schema.sql` and listed in `contracts/db/grants.csv`.
- ADR-0067's status line becomes `Partially superseded by ADR-0084` when this
  record is accepted, and not before.

## Compliance

- A recommendations test: a product that is not `active` and matches a signal is
  counted as `not_sellable`, never `inactive`, and gets an alternative when a
  declared substitute is available.
- A test that the `elsewhere` store is the nearest by haversine distance, that a
  tie goes to the lowest `store_id`, and that a store without coordinates is
  skipped.
- A test that a floor role naming a store id that does not exist gets 403
  `forbidden_store`, and a non-floor role gets 422 `unknown_store`.

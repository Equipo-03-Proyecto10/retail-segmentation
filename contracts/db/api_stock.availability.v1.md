# `api_stock.availability_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_stock.availability_v1` |
| Kind | PostgreSQL view |
| Owning boundary | stock |
| Producer | stock-service |
| Consumers | recommendations |

The view publishes whether a product can be sold in a store and channel
(ADR-0061). Stock holds one on-hand quantity per store and product; a quantity
per channel does not exist, so availability is derived and the view copies no
state. It is the same definition stock-service answers on
`GET /v1/availabilities`, so a recommendation and a client cannot disagree.

## Rows

One row for every combination of a store, a product and a channel. A pair of
store and product with no `inventory` row still has its rows, with
`reason = 'NO_STOCK_RECORD'`.

A row is available when all three tests pass:

1. the product's commercial status allows the sale (`api_products`);
2. the store enables the channel (`api_stores`);
3. `quantity_on_hand` is above zero (`public.inventory`).

The view reads the first two only through their producers' versioned views
(`api_products.product_ref_v1` and its successors, and the store directory's
channel view), never through their tables. Those columns and views are
published by the products and stores contracts of this phase (F21-11, F21-12);
this view is not created until they exist.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `store_id` | `smallint` | Store. Part of the row's identity. |
| `product_id` | `integer` | Product. Part of the row's identity. |
| `channel_id` | `smallint` | Channel. Part of the row's identity. |
| `is_available` | `boolean` | True only when all three tests pass. |
| `reason` | `text` | `AVAILABLE`, or the first failing test in this order: `NOT_SELLABLE`, `CHANNEL_NOT_ENABLED`, `NO_STOCK_RECORD`, `OUT_OF_STOCK`. |
| `quantity_on_hand` | `integer` | Units held; `NULL` when the pair has no stock record. A missing record is not a quantity of zero. |
| `updated_at` | `timestamptz` | When the quantity last changed; `NULL` with it. |

`(store_id, product_id, channel_id)` is unique, and `is_available` is true
exactly when `reason = 'AVAILABLE'`.

## Access and consumers

The only consumer is **recommendations**. It receives `USAGE` on `api_stock`
(already held for `stock_level_v1`) and `SELECT` on this view only, and names
the columns it reads. stock-service holds `SELECT` as the producer. The view
runs with its owner's rights (`security_invoker = off`), so these grants expose
neither `public.inventory` nor the other boundaries' tables.

A change of availability reaches recommendations as `stream:stock.v1.changed`
when stock changes. A change of the product's status or of a store's channels is
announced by the products and stores streams; the recommendations group
re-reads this view for either (ADR-0044). Current state is never taken from an
event.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Adding a `reason` value is breaking, because a consumer branches on it.
Removing or renaming a consumed column, changing its type, the
one-row-per-triple rule, or the order that picks `reason` is breaking. A
breaking revision is published as `api_stock.availability_v2` with a matching
`.v2.md` contract beside v1; v1 remains until recommendations has moved.

# `api_stock.stock_level_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_stock.stock_level_v1` |
| Kind | PostgreSQL view |
| Owning boundary | stock |
| Producer | stock-service |
| Consumers | recommendations |

The view publishes one row for every row in `public.inventory`. Consumers
never receive privileges on the base table, and the view copies no state.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `store_id` | `smallint` | Store holding the stock. Part of the row's identity. |
| `product_id` | `integer` | Product held. Part of the row's identity. |
| `quantity_on_hand` | `integer` | Units held, never negative. |
| `updated_at` | `timestamptz` | When the quantity last changed. |

`(store_id, product_id)` is unique. A pair with no row has no stock record,
which is not the same as a quantity of zero.

## Access and consumers

The only consumer is **recommendations**. It receives `USAGE` on `api_stock`
and `SELECT` on this view only, and names the columns it reads. stock-service
holds `SELECT` as the producer.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its type, or changing the
one-row-per-pair rule is breaking. A breaking revision is published as
`api_stock.stock_level_v2` with a matching `.v2.md` contract beside v1; v1
remains until recommendations has moved.

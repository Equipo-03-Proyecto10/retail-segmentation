# `api_stock.restock_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_stock.restock_v1` |
| Kind | PostgreSQL function |
| Owning boundary | stock |
| Producer | stock-service |
| Consumer | sales |
| Signature | `api_stock.restock_v1(smallint, int, int, bigint)` |
| regprocedure | `api_stock.restock_v1(smallint,integer,integer,bigint)` |
| Result | `int`: the new `quantity_on_hand` |

It restores stock when a sale is reversed (ADR-0061, ADR-0062), and is the
counterpart of [`api_stock.consume_v1`](api_stock.consume.v1.md). Together they
are the only writes that cross a service boundary (E-05). It adds the returned
units to `inventory.quantity_on_hand` with a conditional `UPDATE ... RETURNING`
and appends one `RESTOCK` row to `stock.stock_movement` in the same statement
block, so the quantity and its history cannot disagree. Stock is never negative:
a restock only adds.

## Arguments

| Position | Name | Type | Meaning |
|---:|---|---|---|
| 1 | `store_id` | `smallint` | Store whose stock is restored. |
| 2 | `product_id` | `integer` | Product returned. |
| 3 | `quantity` | `integer` | Units to add; must be greater than 0. |
| 4 | `reversal_id` | `bigint` | The sale reversal that returned the units. Stored on the movement as `reversal_id`. |

Callers cast the store id: `SELECT api_stock.restock_v1($1::smallint, $2, $3, $4)`.
An uncast integer finds no function and fails with SQLSTATE 42883.

## Errors

| SQLSTATE | Constraint name | Meaning | Sales maps it to |
|---|---|---|---|
| 22023 | none | `quantity` is NULL or at most 0, or `reversal_id` is NULL. Raised before any row is touched. | `invalid_quantity` |
| 23514 | `no_stock_record` | No `inventory` row exists for the pair. | `no_stock_record` |
| 23505 | `restock_already_recorded` | A `RESTOCK` for this `(reversal_id, store_id, product_id)` exists. The unit of work is never applied twice. | `restock_already_recorded` |

These are database outcomes. Sales maps them to HTTP; stock-service never
answers them. A line the reversal marks as not resalable is not passed to this
function at all: sales decides that, and stock holds no opinion on why units
return.

## Security and use

- SECURITY DEFINER, owned by `definer_stock`, with
  `search_path = public, pg_temp`.
- EXECUTE is granted to `svc_sales` only, and to the owner `definer_stock`.
  `definer_stock` also holds the `INSERT` on `stock.stock_movement` and the
  column `UPDATE`s on `public.inventory` that the function needs.
- A caller invokes it once per reversed line, in ascending `product_id` order,
  inside the reversal's own transaction (ADR-0062). The ordering keeps two
  reversals that share products from deadlocking, and a rollback of the
  reversal restores nothing, because nothing was committed.
- The `inventory` change fires the capture trigger, so
  `stream:stock.v1.changed` announces it like any other writer's.

## Compatibility

Changing the signature, the result, an error code or constraint name, or the
atomic behavior is breaking. A breaking change ships `api_stock.restock_v2`
beside `restock_v1`, with its own `.v2.md` contract; v1 is removed only after
sales has moved.

# `api_stock.consume_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_stock.consume_v1` |
| Kind | PostgreSQL function |
| Owning boundary | stock |
| Producer | stock-service |
| Consumer | sales |
| Signature | `api_stock.consume_v1(smallint, int, int)` |
| regprocedure | `api_stock.consume_v1(smallint,integer,integer)` |
| Result | `int`: the remaining `quantity_on_hand` |

It is the only write that crosses a service boundary (E-05). It runs the
conditional `UPDATE ... WHERE quantity_on_hand >= quantity RETURNING` that the
web's decrement uses, so stock is never negative.

Its counterpart, which restores stock when a sale is reversed, is
[`api_stock.restock_v1`](api_stock.restock.v1.md); E-05 names both.

## Arguments

| Position | Name | Type | Meaning |
|---:|---|---|---|
| 1 | `store_id` | `smallint` | Store whose stock is consumed. |
| 2 | `product_id` | `integer` | Product consumed. |
| 3 | `quantity` | `integer` | Units to consume; must be greater than 0. |

Callers cast the store id: `SELECT api_stock.consume_v1($1::smallint, $2, $3)`.
An uncast integer finds no function and fails with SQLSTATE 42883.

## Errors

| SQLSTATE | Constraint name | Meaning | Sales maps it to |
|---|---|---|---|
| 22023 | none | `quantity` is NULL or at most 0. Raised before any row is touched. | `invalid_quantity` |
| 23514 | `no_stock_record` | No `inventory` row exists for the pair. | `no_stock_record` |
| 23514 | `stock_unavailable` | The row holds less than `quantity`. | `stock_unavailable` |

These are database outcomes. Sales maps them to HTTP; stock-service never
answers them.

## Security and use

- SECURITY DEFINER, owned by `definer_stock`, with
  `search_path = public, pg_temp`.
- EXECUTE is granted to `svc_sales` only.
- A caller invokes it once per sale line, in ascending `product_id` order,
  inside its own transaction (ADR-0039). The ordering keeps two sales that
  share products from deadlocking, and a rollback of the sale restores every
  decrement.

## Compatibility

Changing the signature, the result, an error code or constraint name, or the
atomic behavior is breaking. A breaking change ships `api_stock.consume_v2`
beside `consume_v1`, with its own `.v2.md` contract; v1 is removed only after
sales has moved.

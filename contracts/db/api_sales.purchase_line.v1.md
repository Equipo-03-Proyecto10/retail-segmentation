# `api_sales.purchase_line_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_sales.purchase_line_v1` |
| Kind | PostgreSQL view |
| Owning boundary | sales |
| Producer | sales-service |
| Consumers | recommendations |

The view publishes one row for every sale line, whether the sale came through
`POST /v1/sales` or through the web's CSV path. Both paths write the same
`transaction` and `transaction_line` tables, so the view cannot tell them apart
and consumers must not try to. A sale with three lines is three rows that share
a `transaction_id`. Consumers name only the columns they read.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `transaction_id` | `bigint` | The sale. It is the `sale_id` of the sales contract. |
| `customer_id` | `uuid` | The customer who bought. |
| `store_id` | `smallint` | The store where the sale was made. |
| `channel_id` | `smallint` | The sales channel. |
| `occurred_at` | `timestamp with time zone` | When the sale happened, as accepted. |
| `product_id` | `integer` | The product on this line. |
| `quantity` | `integer` | Units sold; always positive. |
| `unit_price` | `numeric(10,2)` | The price actually charged for one unit (RN-13). |

`(transaction_id, product_id)` is unique: a product appears once per sale.
The view carries no product name, list price or total; consumers read product
state through `api_products.product_ref_v1`.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, or
changing the one-row-per-line rule is breaking. A breaking revision is
published as `api_sales.purchase_line_v2` with a matching `.v2.md` contract
beside v1; v1 remains until recommendations has moved.

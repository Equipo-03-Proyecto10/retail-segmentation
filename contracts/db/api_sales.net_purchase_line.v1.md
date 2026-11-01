# `api_sales.net_purchase_line_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_sales.net_purchase_line_v1` |
| Kind | PostgreSQL view |
| Owning boundary | sales |
| Producer | sales-service |
| Consumers | recommendations |

The view publishes the purchases that still count: one row for each line of an
accepted sale that has at least one unit left after its reversals. It is the
single definition of net units for services (ADR-0062). The web reads the same
definition through `public.net_sale_line`, so a service and the web cannot
disagree about what a customer bought.

A sale is never edited, so `transaction_line.quantity` stays what was sold. A
cancellation or a return adds a reversal and raises the line's
`returned_quantity`; this view subtracts it. A line with no units left does not
appear, and a sale whose every line is gone stops counting as a purchase. Like
`api_sales.purchase_line_v1`, the view cannot tell a sale recorded through
`POST /v1/sales` from one loaded by the web's CSV path. Consumers name only the
columns they read.

Use this view to know what a customer bought, what they spent and when. Do not
use it to find a sale that has been reversed in full: such a sale has no row here.
To resolve the customer or the store of a sale that no longer appears, read
`api_sales.purchase_line_v1`, which keeps its meaning and still holds every
accepted line, gross.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `transaction_id` | `bigint` | The sale. It is the `sale_id` of the sales contract. |
| `customer_id` | `uuid` | The customer who bought. |
| `store_id` | `smallint` | The store where the sale was made. |
| `channel_id` | `smallint` | The sales channel. |
| `occurred_at` | `timestamp with time zone` | When the sale happened, as accepted. A late sale counts by this instant, never by when it was recorded. |
| `product_id` | `integer` | The product on this line. |
| `quantity` | `integer` | Units sold, as accepted; always positive. |
| `returned_quantity` | `integer` | Units reversed so far, between 0 and `quantity`. |
| `net_quantity` | `integer` | `quantity - returned_quantity`; always positive in this view. |
| `unit_price` | `numeric(10,2)` | The price actually charged for one unit (RN-13). |
| `net_amount` | `numeric(12,2)` | `net_quantity * unit_price`. |

`(transaction_id, product_id)` is unique. A sale's net amount is the sum of
`net_amount` over its rows. The view carries no product name, list price or
total, and no reversal reason or kind: consumers read product state through
`api_products.product_ref_v1`, and reversals through the sales contract.

## What the view promises

- Every row has `net_quantity > 0`.
- `returned_quantity` equals the sum of that line's quantities in the reversal
  lines, written in the same transaction that recorded the reversal. A reader
  never sees a reversal without its effect.
- A returned line that cannot be resold still reduces `net_quantity`:
  `resellable` decides stock, not what the customer bought.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, or changing
the rule that a row is a line with net units left is breaking. A breaking
revision is published as `api_sales.net_purchase_line_v2` with a matching
`.v2.md` contract beside v1; v1 remains until its consumers have moved.

## Grants

The view's `USAGE` and `SELECT` rows in `contracts/db/grants.csv` are added in the
same pull request as its definition in `sql/01_schema.sql`, which is the
implementation story of the reversal contract (F21-03). Each further consumer is
added to the table above by the story that grants it.

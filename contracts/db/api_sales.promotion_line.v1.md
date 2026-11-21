# `api_sales.promotion_line_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_sales.promotion_line_v1` |
| Kind | PostgreSQL view |
| Owning boundary | sales |
| Producer | sales-service |
| Consumer | campaigns-service |

The view publishes one row for each phone-sale line that received a discount
through a promotion and still has at least one unit after reversals. It joins
the line's charged price to the sale-time list price and promotion recorded in
`sales.sale_line_price` by F25-05. CSV and imported lines have no row in that
table, so they do not appear and cannot contribute to campaign spending.

The view is net of reversals under ADR-0062. It reads
`public.net_sale_line`, so `net_quantity` is the units still counted and a
fully reversed line disappears. A consumer calculates the line's granted
discount as `(list_price - unit_price) * net_quantity`.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `transaction_id` | `bigint` | The sale; it is the `sale_id` of the sales HTTP contract. |
| `customer_id` | `uuid` | The customer who received the discount. |
| `product_id` | `integer` | The product on the sale line. |
| `promotion_id` | `integer` | The campaigns-owned promotion that priced the line. |
| `net_quantity` | `integer` | Units left after reversals; always positive. |
| `unit_price` | `numeric(10,2)` | Price actually charged for one unit. |
| `list_price` | `numeric(10,2)` | Product list price read in the sale's own transaction. |

`(transaction_id, product_id)` is unique. Every row has
`list_price > unit_price`, because a line without a positive granted discount
does not contribute to spending.

## What the view promises

- A promotion appears only when sales-service retained it on a discounted
  phone-sale line.
- A partial reversal reduces `net_quantity`; a full reversal removes the row.
- The prices are historical facts from the sale, not the product's current
  catalog price.
- Imported and CSV lines are absent because no historical list price or
  promotion is recorded for them.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, admitting
lines without a positive discount, or changing the net-of-reversals rule is
breaking. A breaking revision is published as
`api_sales.promotion_line_v2` with a matching `.v2.md` beside v1.

## Grants

`svc_campaigns` receives `SELECT`; it already has `USAGE` on `api_sales`.
Both privileges are recorded in `contracts/db/grants.csv` and the view remains
unavailable to `PUBLIC`.

# `api_recommendations.recommended_purchase_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_recommendations.recommended_purchase_v1` |
| Kind | PostgreSQL view |
| Owning boundary | recommendations |
| Producer | recommendations-service |
| Consumers | analytics-kpis |

The view publishes the sale lines bought through a recommendation (ADR-0090): a net line of an
accepted sale (ADR-0062) whose customer made a `RECO_CLICK` on the same product no more than seven
days before the sale. It is observational: a click preceded a purchase, and nothing says the
recommendation caused it. analytics-kpis reads it for `recommendation-performance` (F26-04, #849)
and counts its rows apart from impressions and clicks. A definer-rights view: a consumer needs no
privilege on the interactions or on the sales view it reads. Consumers name only the columns they read.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `transaction_id` | `bigint` | The sale. |
| `product_id` | `integer` | The product of the line. |
| `customer_id` | `uuid` | The customer who clicked and bought. |
| `store_id` | `smallint` | The store of the sale. |
| `occurred_at` | `timestamp with time zone` | When the sale happened. |
| `net_quantity` | `integer` | Units left after reversals; always positive. |
| `net_amount` | `numeric(12,2)` | `net_quantity` times the price charged. |
| `click_event_id` | `uuid` | The earliest qualifying click, the one credited. |
| `click_occurred_at` | `timestamp with time zone` | When that click happened. |
| `data_origin` | `varchar(20)` | `OBSERVED` or `SEEDED` (ADR-0052). A consumer marks a figure built on any `SEEDED` row as synthetic. |

`(transaction_id, product_id)` is unique.

## What is a row

- A sale line of a customer with a non-voided click on the same product in the seven days up to the sale. The click must not be later than the sale.
- An impression earns nothing. A sale without a customer, a click without a customer and a click on another product earn nothing.
- Several qualifying clicks give one row, naming the earliest.
- A sale reversed in full has no row, because it has none in `api_sales.net_purchase_line_v1`.
- The figure can grow after it was first read: a click can reach recommendations after the sale (eventual consistency, ADR-0075).

## Compatibility

Adding a column is compatible, because consumers name the columns they read. Removing or
renaming a consumed column, changing its type, or changing what a row or a column means (the
window or the credited interaction included) is breaking. Such a change is published as
`api_recommendations.recommended_purchase_v2`, with a matching `.v2.md` contract beside this
one, and v1 stays until analytics-kpis has moved.

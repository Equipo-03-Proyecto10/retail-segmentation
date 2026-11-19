# `api_campaigns.promotion_window_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_campaigns.promotion_window_v1` |
| Kind | PostgreSQL view |
| Owning boundary | campaigns |
| Producer | campaigns-service |
| Consumers | sales |

The view publishes every promotion a sale may have relied on, on whatever day the
sale happened. It holds one row for each store and channel of each promotion
whose campaign is `ACTIVE` or `FINISHED`, so a promotion that applies to two
stores and two channels is four rows sharing a `promotion_id`. A promotion of a
`DRAFT` campaign never ran and one of a `CANCELLED` campaign was withdrawn, so
neither has a row.

Unlike [`api_campaigns.active_promotion_v1`](api_campaigns.active_promotion.v1.md),
the view applies no date test. The consumer decides which day a fact belongs to:
sales judges a line against the sale's own day, `occurred_at` in its
`SALES_TIMEZONE`, so a sale queued offline on a promotion's last day and synced
the next morning is judged on the day it happened, and the database session's
time zone plays no part (F25-05, #1108 review).

The view does not say how overlapping promotions combine, nor whether the store's
own cap (`max_discount_percent` in `api_stores.store_directory_v2`) is lower. Both
are the consumer's decision. The view carries no customer. Consumers name only
the columns they read.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `promotion_id` | `integer` | The promotion, as `promotion_id` of the campaigns contract. |
| `campaign_id` | `integer` | The campaign that owns it. |
| `store_id` | `smallint` | A store where the promotion applies. |
| `channel_id` | `smallint` | A channel on which the promotion applies. |
| `discount_percent` | `numeric(5,2)` | The discount the offer states, above 0.00 and at most 100.00. |
| `starts_on` | `date` | First day of the validity, inclusive. |
| `ends_on` | `date` | Last day of the validity, inclusive. |

`(promotion_id, store_id, channel_id)` is unique.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, changing
which campaign statuses are published, adding a date test, or changing the
one-row-per-store-and-channel rule is breaking, and is published as
`api_campaigns.promotion_window_v2` with a matching `.v2.md` contract beside v1.

## Grants

`contracts/db/grants.csv` carries `USAGE` on `api_campaigns` and `SELECT` on this
view for the producer and for sales (#1104).

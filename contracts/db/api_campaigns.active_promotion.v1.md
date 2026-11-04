# `api_campaigns.active_promotion_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_campaigns.active_promotion_v1` |
| Kind | PostgreSQL view |
| Owning boundary | campaigns |
| Producer | campaigns-service |
| Consumers | sales |

The view publishes the promotions a sale may rely on today. It holds one row
for each store and channel of each active promotion, so a promotion that applies
to two stores and two channels is four rows that share a `promotion_id`. A
promotion is active when its campaign's status is `ACTIVE` and the database's
`CURRENT_DATE` lies within `starts_on` and `ends_on`, both inclusive. A
promotion of a DRAFT, FINISHED or CANCELLED campaign has no row, and neither
does one whose validity has not begun or has passed. The date test is made by
the view when it is read, so no job has to retire a row.

Sales reads the view to validate a discount (F25-05): a sale at a store and
through a channel may grant a discount only up to the `discount_percent` of a
row for that pair. The view does not say how overlapping promotions combine,
nor whether the store's own cap (`max_discount_percent` in
`api_stores.store_directory_v2`) is lower. Both are the consumer's decision.
Consumers name only the columns they read. The view carries no customer: an
audience, its exclusions and its eligibility rules stay inside campaigns.

The producing tables are created by the schema story that implements the
contract; this contract names none.

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
the meaning of "active", or changing the one-row-per-store-and-channel rule is
breaking. A breaking revision is published as
`api_campaigns.active_promotion_v2` with a matching `.v2.md` contract beside
v1; v1 remains until sales has moved.

# `api_stores.store_hours_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_stores.store_hours_v1` |
| Kind | PostgreSQL view |
| Owning boundary | stores |
| Producer | stores-service |
| Consumers | campaigns |

The view publishes one row for each day a store opens, as written through
`PUT /v1/stores/{store_id}/opening-hours`. A day the store is closed has no
row, and a store with no hours has none. Times are read in the store's
`time_zone`, which `api_stores.store_commerce_v1` publishes.

The view is published ahead of its implementation. The table behind it, the
view itself and the grants in `contracts/db/grants.csv` arrive with F21-12.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `store_id` | `smallint` | Store identifier; matches `store_directory_v1.store_id`. |
| `day_of_week` | `character varying(9)` | `MONDAY` through `SUNDAY`. |
| `opens_at` | `time without time zone` | Opening time, local to the store. |
| `closes_at` | `time without time zone` | Closing time, local to the store; always later than `opens_at`. |

`(store_id, day_of_week)` is unique: a store has at most one interval a day,
and an interval never crosses midnight.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, adding a
second interval for a day, or letting an interval cross midnight is breaking.
A breaking revision is published as `api_stores.store_hours_v2` with a
matching `.v2.md` contract beside v1; v1 remains until every consumer has
moved.

# `api_stores.store_channel_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_stores.store_channel_v1` |
| Kind | PostgreSQL view |
| Owning boundary | stores |
| Producer | stores-service |
| Consumers | sales, stock, campaigns |

The view publishes one row for each channel enabled at a store, as written
through `PUT /v1/stores/{store_id}/enabled-channels`. A store with no enabled
channel has no row. The channel vocabulary itself belongs to sales; this view
carries channel ids only, and a consumer that needs a name resolves the id
with sales.

The view is published ahead of its implementation. The table behind it, the
view itself and the grants in `contracts/db/grants.csv` arrive with F21-12.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `store_id` | `smallint` | Store identifier; matches `store_directory_v1.store_id`. |
| `channel_id` | `smallint` | Identifier of a channel enabled at the store. |

`(store_id, channel_id)` is unique.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, or
changing the one-row-per-enabled-channel rule is breaking. A breaking revision
is published as `api_stores.store_channel_v2` with a matching `.v2.md` contract
beside v1; v1 remains until every consumer has moved.

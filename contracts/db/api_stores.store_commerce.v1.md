# `api_stores.store_commerce_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_stores.store_commerce_v1` |
| Kind | PostgreSQL view |
| Owning boundary | stores |
| Producer | stores-service |
| Consumers | sales, stock, campaigns |

The view publishes one row for every row in `public.store`: the store's
operational status, its time zone and its commercial rules, as written through
`PUT /v1/stores/{store_id}/status`, `/opening-hours` and `/commercial-rules`
(`contracts/stores/openapi.yaml`, 1.2.0). A store nobody has written to still
has a row, with the defaults below. Consumers name only the columns they read.

The view is published ahead of its implementation. The tables behind it, the
view itself and the grants in `contracts/db/grants.csv` arrive with F21-12.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `store_id` | `smallint` | Stable store identifier. |
| `status` | `character varying(20)` | `ACTIVE`, `TEMPORARILY_CLOSED` or `CLOSED`. A store whose status was never written is `ACTIVE`. |
| `status_changed_at` | `timestamp with time zone` | When the status was last written; `NULL` when it never was. |
| `time_zone` | `character varying(64)` | IANA time zone the store's opening hours are read in; `NULL` when no hours were set. |
| `max_discount_percent` | `numeric(5,2)` | The largest discount, as a percentage of the line price, that a sale at this store may apply, from 0.00 through 100.00; `NULL` when the store set no limit. F25-05 enforces it in sales. |

`status` is stores-service's own fact. It is independent of the catalog flag
`is_active` that `api_stores.store_directory_v1` publishes, and neither
implies the other.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, adding a
status value, changing the one-row-per-store rule, or changing what `NULL`
means in a column is breaking. A breaking revision is published as
`api_stores.store_commerce_v2` with a matching `.v2.md` contract beside v1; v1
remains until every consumer has moved.

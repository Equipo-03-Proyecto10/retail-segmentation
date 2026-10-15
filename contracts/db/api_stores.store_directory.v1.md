# `api_stores.store_directory_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_stores.store_directory_v1` |
| Kind | PostgreSQL view |
| Owning boundary | stores |
| Producer | stores-service |
| Consumers | check-ins, engagement |

The view publishes one row for every row in `public.store`. It left-joins the
optional `stores.store_location`, so a store remains in the directory when no
location has been set. Consumers name only the columns they read.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `store_id` | `smallint` | Stable store identifier. |
| `name` | `character varying(100)` | Store display name. |
| `city` | `character varying(80)` | Store city. |
| `state` | `character varying(80)` | Store state. |
| `is_active` | `boolean` | Whether the store is active. |
| `latitude` | `numeric` | Latitude from −90 through 90; `NULL` when no location is set. |
| `longitude` | `numeric` | Longitude from −180 through 180; `NULL` when no location is set. |
| `geofence_radius_m` | `integer` | Geofence radius from 25 through 2000 metres; `NULL` when no location is set. |

The last three columns are all `NULL` together or all non-`NULL`; a partial
location is not a valid row.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, changing
the one-row-per-store rule, or changing the all-or-none location nullability is
breaking. A breaking revision is published as
`api_stores.store_directory_v2` with a matching `.v2.md` contract beside v1;
v1 remains until both consumers have moved.

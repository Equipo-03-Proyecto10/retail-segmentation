# `api_stores.store_directory_v2`

## Contract

| Field | Value |
|---|---|
| Object | `api_stores.store_directory_v2` |
| Kind | PostgreSQL view |
| Owning boundary | stores |
| Producer | stores-service |
| Consumers | stock, campaigns, sales |
| Supersedes | nothing: [`api_stores.store_directory_v1`](api_stores.store_directory.v1.md) stays for check-ins and engagement |

This is a new version, not a new view. It publishes everything v1 does, with
the same columns, types and meanings, and adds the store's operating status,
opening hours, enabled channel ids and commercial rule. Adding those columns to
v1 would be compatible for a consumer that names its columns, but the stores
contract and the view move together, and v1's consumers have no reason to be
touched by this change. v1 is removed only after check-ins and engagement
have moved to v2.

The view publishes one row for every row in `public.store`. It left-joins the
service-owned tables that hold the optional location, status, hours, enabled
channels and commercial rule, so a store whose facts were never written is
still in the directory, with the defaults below. The producing tables are
created by the schema story that implements the contract (#786); this contract
names no table.

Consumers name only the columns they read.

## Columns

The first eight columns are exactly the columns of v1.

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `store_id` | `smallint` | Stable store identifier. |
| `name` | `character varying(100)` | Store display name. |
| `city` | `character varying(80)` | Store city. |
| `state` | `character varying(80)` | Store state. |
| `is_active` | `boolean` | Whether the store is active in the directory (written only by the web). |
| `latitude` | `numeric` | Latitude from −90 through 90; `NULL` when no location is set. |
| `longitude` | `numeric` | Longitude from −180 through 180; `NULL` when no location is set. |
| `geofence_radius_m` | `integer` | Geofence radius from 25 through 2000 metres; `NULL` when no location is set. |
| `status` | `text` | Operating status written through stores-service: `OPEN`, `TEMPORARILY_CLOSED` or `CLOSED`. Never `NULL`; `OPEN` when never written. |
| `time_zone` | `text` | IANA time zone the opening hours are expressed in; `NULL` when no hours are set. |
| `opening_hours` | `jsonb` | A JSON array of `{"day_of_week": 1-7, "opens_at": "HH:MM", "closes_at": "HH:MM"}` objects, ISO 8601 days (1 Monday), ordered by `day_of_week`, at most one per day. A day not listed is closed. Never `NULL`; `[]` when no hours are set. |
| `channel_ids` | `smallint[]` | Ascending ids of the channels the store enables, each an existing `channel.channel_id`. Never `NULL`; empty when none are enabled. |
| `max_discount_percent` | `numeric(5,2)` | The largest discount the store allows on a sale line, from 0.00 through 100.00; `NULL` when never set, which means the store imposes no cap of its own. |

The location columns are `NULL` together or non-`NULL` together, as in v1.
`time_zone` is `NULL` exactly when `opening_hours` is `[]`.

A store can sell on a channel only when `is_active` is true, `status` is
`OPEN` and the channel id is in `channel_ids`. The view publishes the facts; the
consumer applies the rule.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, changing
the one-row-per-store rule, the all-or-none location nullability, the
non-`NULL` defaults of `status`, `opening_hours` and `channel_ids`, or the
shape of an `opening_hours` element is breaking, and is published as
`api_stores.store_directory_v3` with a matching `.v3.md` contract beside this
one.

## Grants

`contracts/db/grants.csv` is not changed by the contract-only story. It is
compared with the live database in both directions, and the view does not exist
there yet. The view, the `USAGE` on `api_stores` and the `SELECT` rows for the
consumers above are added with the DDL in #786.

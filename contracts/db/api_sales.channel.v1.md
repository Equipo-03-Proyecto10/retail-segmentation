# `api_sales.channel_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_sales.channel_v1` |
| Kind | PostgreSQL view |
| Owning boundary | sales |
| Producer | sales-service |
| Consumers | stock |

The view publishes the ids of the sales channels. sales is the read owner of
`public.channel` (E-10): the web writes it, and sales validates every sale's
`channel_id` against it. Another boundary that needs the set of channels reads
it here and never from the table.

stock needs the set, not a reference: `api_stock.availability_v1` has one row
for every store, product and channel, including the channels a store does not
enable (F21-07, [contract](api_stock.availability.v1.md)).

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `channel_id` | `smallint` | Stable channel identifier, one row per channel. |

Channel names are not published: no consumer needs them, and a client reads
them from sales' `channels` resource.

## Access and consumers

The only consumer is **stock**. It receives `USAGE` on `api_sales` and `SELECT`
on this view only. sales holds `SELECT` as the producer. The view runs with its
owner's rights (`security_invoker = off`), so the grants expose nothing else of
`public.channel`.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming `channel_id`, changing its type, or the one-row-per-channel
rule is breaking, and is published as `api_sales.channel_v2` with a matching
`.v2.md` contract beside this one.

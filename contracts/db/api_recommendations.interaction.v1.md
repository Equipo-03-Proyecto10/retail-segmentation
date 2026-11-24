# `api_recommendations.interaction_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_recommendations.interaction_v1` |
| Kind | PostgreSQL view |
| Owning boundary | recommendations |
| Producer | recommendations-service |
| Consumers | analytics-kpis |

The view publishes the interactions with recommendations that still count: one row per
`RECO_IMPRESSION` or `RECO_CLICK` that engagement recorded and has not voided. recommendations
keeps them from `stream:engagement.v1.interaction_recorded` (ADR-0075), so the view is
eventually consistent, within seconds. analytics-kpis reads it for `conversion` and
`recommendation-performance` (F26-04, #849) and counts rows: an impression and a click are two
kinds of row, and neither is derived from the other. Consumers name only the columns they read.
Its definition is in the `recommendations` boundary section of `sql/01_schema.sql`.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `event_id` | `uuid` | The engagement event. One row per event. |
| `kind` | `text` | `RECO_IMPRESSION` or `RECO_CLICK`. |
| `occurred_at` | `timestamp with time zone` | When the interaction happened, as engagement recorded it. |
| `store_id` | `smallint` | The store where it happened. |
| `product_id` | `integer` | The recommended product. |
| `customer_id` | `uuid` | The customer, when the client sent one; `NULL` for an anonymous interaction. |
| `label_code` | `text` | The segment label of the recommendation. |
| `rank` | `smallint` | The position of the recommendation, from 1. |

## What is a row

- A voided event has no row, whichever of its two notices arrived first.
- An anonymous interaction has a row with a `NULL` `customer_id`. It counts as an impression or a click and can never become a purchase.
- A repeated delivery of the same event is one row.

## Compatibility

Adding a column is compatible, because consumers name the columns they read. Removing or
renaming a consumed column, changing its type, or changing what a row or a column means is
breaking. Such a change is published as `api_recommendations.interaction_v2`, with a matching
`.v2.md` contract beside this one, and v1 stays until analytics-kpis has moved.

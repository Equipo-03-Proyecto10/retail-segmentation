# `api_checkins.customer_last_visit_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_checkins.customer_last_visit_v1` |
| Kind | PostgreSQL view |
| Owning boundary | check-ins |
| Producer | check-ins-service |
| Consumers | customers |

The view publishes, for each customer with at least one recorded visit, the time
of that customer's latest visit. A visit is a row of `checkins.customer_visit`,
recorded by `POST /v1/customer-visits` with the customer's consent (#801). The
view exists so that customers-service can apply ADR-0066's retention rule ("no
sale and no check-in for 36 months") without reading the check-ins tables.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `customer_id` | `uuid` | The customer. One row per customer; never null. |
| `last_visited_at` | `timestamp with time zone` | The latest `occurred_at` among the customer's visits. |

A customer with no visit has no row. A visit whose customer reference has been
removed, because a maintenance act nulled it, no longer counts: the view reads
only visits that still name a customer. The view carries no store, campaign or
recorder, so it says when a customer was last seen and nothing about where or by
whom.

## Consumers

- **customers** reads `customer_id` and `last_visited_at` to list the retention
  candidates of `GET /v1/customers/retention-candidates` (ADR-0066 section 3, #1061).

## Compatibility

Adding a column is compatible because consumers name the columns they read.
Removing or renaming a column, changing its type, or changing which visits count
is breaking. A breaking revision ships `api_checkins.customer_last_visit_v2` and a
`.v2.md` contract beside v1, and v1 stays until customers has moved.

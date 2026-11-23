# `api_segments.assignment_interval_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_segments.assignment_interval_v1` |
| Kind | PostgreSQL view |
| Owning boundary | segments |
| Producer | segments |
| Consumers | model-registry |

The view publishes every assignment a customer has had, each with the instant it opened
and the instant it closed, so a consumer can tell which segment a customer was in at any
past instant. model-registry reads it for the outcome test (F23-11, ADR-0065, ADR-0089).
A cohort at `t` is the set of customers whose assignment was open at `t`. The view
reports what the segment history recorded and fills in nothing from any other source.
Consumers name only the columns they read. Its definition is in the `segments` boundary
section of `sql/01_schema.sql`. It needs nothing that a later section creates, so an
operator applies that section alone.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `customer_id` | `uuid` | The customer, as in `api_customers.customer_ref_v1`. |
| `label_code` | `character varying(40)` | The label of this assignment; `NULL` when the run found no label (RN-21). |
| `run_id` | `bigint` | The run that wrote the assignment. Its model version is model-registry's, read by `run_id` (ADR-0064). |
| `valid_from` | `timestamp with time zone` | The instant the assignment opened. |
| `valid_to` | `timestamp with time zone` | The instant it closed; `NULL` while it is the customer's current assignment. |
| `data_origin` | `text` | `SEEDED` when the run that wrote it is seeded, else `OBSERVED` (ADR-0052). |

## What is a row

- A row is one assignment of `customer_segment_history`, for a customer that
  `api_customers.customer_ref_v1` lists.
- Intervals are half-open, `[valid_from, valid_to)`. A customer's intervals never
  overlap (the history's exclusion constraint). So at any instant `t` a customer has at
  most one row with `valid_from <= t` and `valid_to` null or later than `t`.
- An assignment with no label is a row with a `NULL` `label_code`. It is the run's
  result of no label, not a missing customer.

## Compatibility

Adding a column is compatible because consumers name the columns they read. Removing or
renaming a consumed column, changing its type, or changing what an interval means is
breaking. Such a change is published as `api_segments.assignment_interval_v2`, with a
matching `.v2.md` contract beside this one. v1 remains until model-registry has moved.

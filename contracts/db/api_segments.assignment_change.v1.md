# `api_segments.assignment_change_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_segments.assignment_change_v1` |
| Kind | PostgreSQL view |
| Owning boundary | segments |
| Producer | segments-service |
| Consumers | analytics-kpis |

The view publishes one row for every change of a customer's label between two
consecutive assignments. It reports what the web's segmentation runs recorded,
with the model version of each side from `api_models.run_version_v1` (ADR-0064),
and fills in nothing from any other source. Consumers name only the columns they
read. Its definition is in the `analytics-kpis` boundary section of
`sql/01_schema.sql`, because it joins `api_models.run_version_v1`, which the
model-registry section creates after segments' section; the section an operator
applies before the release that reads it is therefore that one.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `customer_id` | `uuid` | The customer, as in `api_customers.customer_ref_v1`. |
| `previous_label_code` | `character varying(40)` | The label the earlier assignment held; `NULL` when that run found no label (RN-21). |
| `new_label_code` | `character varying(40)` | The label the later assignment holds; `NULL` when that run found no label. |
| `previous_version_id` | `bigint` | The model version of the earlier run; `NULL` while model-registry has not linked it. |
| `new_version_id` | `bigint` | The model version of the later run; `NULL` while model-registry has not linked it. |
| `changed_at` | `timestamp with time zone` | The instant the later assignment opened (`valid_from`). |
| `run_id` | `bigint` | The run that wrote the later assignment. |
| `data_origin` | `text` | `SEEDED` when either run is seeded, else `OBSERVED` (ADR-0052). |

## What is a row

- A customer's assignments are ordered by `valid_from`. A row is the later of two
  consecutive assignments whose labels differ, `NULL` counted as one value.
- A customer's first assignment is not a change, so a customer with one assignment has no row.
- A run that leaves the label as it was is not a change, even under a new model version.
- A row whose two versions differ, or either of which is `NULL`, is a **methodological**
  change: the model moved, not necessarily the customer (ADR-0064). Consumers count it
  apart and never as behaviour.

## Reconciliation with `segment-migrations`

`GET /v1/segment-migrations/matrix` compares each customer's state at two instants.
For a customer with one change between them, the matrix cell (label before, label
after) and this view's row agree. A customer with two changes inside the window is one
cell of the matrix (the first label against the last) and two rows here, which is why
the KPI reports `changes` and `customers_migrated` apart.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, or changing
what a row is, is breaking and requires `api_segments.assignment_change_v2` beside this view.

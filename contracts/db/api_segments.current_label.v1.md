# `api_segments.current_label_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_segments.current_label_v1` |
| Kind | PostgreSQL view |
| Owning boundary | segments |
| Producer | segments-service |
| Consumers | recommendations |

The view publishes exactly one row for every customer in
`api_customers.customer_ref_v1`, so a customer is never missing and never
repeated. It reports what the web's segmentation runs recorded and fills in
nothing from any other source. Consumers name only the columns they read.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `customer_id` | `uuid` | The customer. |
| `state` | `text` | One of `assigned`, `unassigned_result` or `no_open_assignment`. |
| `label_code` | `character varying(40)` | The stable label code; `NULL` unless `state` is `assigned`. |
| `since` | `timestamp with time zone` | The open row's `valid_from`; `NULL` when `state` is `no_open_assignment`. |
| `run_id` | `bigint` | The run that wrote the open row; `NULL` when `state` is `no_open_assignment`. |

## The three states

| State | Meaning | `label_code` | `since`, `run_id` |
|---|---|---|---|
| `assigned` | The customer's open row names a label. | set | set |
| `unassigned_result` | The open row is the run's result of no label (RN-21). | `NULL` | set |
| `no_open_assignment` | The customer has no open row, for instance a customer registered after the latest run. | `NULL` | `NULL` |

A customer with no open row is reported as `no_open_assignment`; the view never
substitutes an earlier row, another label or a default.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, adding a
state, changing the meaning of a state, or changing the one-row-per-customer
rule is breaking. A breaking revision is published as
`api_segments.current_label_v2` with a matching `.v2.md` contract beside v1;
v1 remains until recommendations has moved.

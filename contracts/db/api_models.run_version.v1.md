# `api_models.run_version_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_models.run_version_v1` |
| Kind | PostgreSQL view |
| Owning boundary | model-registry |
| Producer | model-registry |
| Consumers | segments |

The view publishes the immutable model-version link for every segmentation run
that model-registry has linked. A run can be temporarily absent while its stream
event is pending; consumers preserve that absence as an unknown version and do
not substitute the currently published version.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `run_id` | `bigint` | The segmentation run, by value across the boundary. |
| `version_id` | `bigint` | The immutable version that scored the run. |
| `linked_at` | `timestamp with time zone` | When model-registry recorded the link. |

## Compatibility

Adding a column is compatible because consumers name selected columns. Removing
or renaming a consumed column, changing its type, or changing the one-link-per-run
rule is breaking and requires `api_models.run_version_v2` beside this view.

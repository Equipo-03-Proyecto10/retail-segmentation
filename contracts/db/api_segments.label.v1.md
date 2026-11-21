# `api_segments.label_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_segments.label_v1` |
| Kind | PostgreSQL view |
| Owning boundary | segments |
| Producer | segments |
| Consumers | model-registry |

The view publishes the stable label vocabulary in its declared best-to-worst order
(ADR-0018, ADR-0030). A K-means cluster has no name of its own: it takes the label at
a position of this order by proportional rank, so a consumer that names clusters needs
the vocabulary and its order and keeps no copy of either. model-registry reads it to
pair the clusters of a training with labels (F22-12). The view carries one row per
label and nothing about any customer.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `label_code` | `character varying(40)` | The stable label code, the vocabulary every segmentation method writes. |
| `ordinal_position` | `smallint` | The label's place in the declared order, from the best (1) to the worst. Unique. |

A consumer reads the labels `ORDER BY ordinal_position`. The names and descriptions of
the labels are the web's presentation and are not part of this contract.

## Compatibility

Adding a column is compatible because consumers name the columns they read. Removing or
renaming a consumed column, changing its type, or changing the meaning of the order
(best to worst) is breaking and is published as `api_segments.label_v2` with a
matching `.v2.md` contract beside this one; v1 remains until model-registry has moved.
Adding a label to the vocabulary is not a change of this contract, but it changes what a
later training pairs its clusters with, and a version records the pairing it used.

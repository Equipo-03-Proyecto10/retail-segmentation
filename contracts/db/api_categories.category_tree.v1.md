# `api_categories.category_tree_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_categories.category_tree_v1` |
| Kind | PostgreSQL view |
| Owning boundary | categories |
| Producer | categories-service |
| Consumers | recommendations |

The view publishes one row for every row in `public.category`, so the whole
tree can be read without a privilege on the base table. A root has a `NULL`
`parent_category_id`. Consumers name only the columns they read.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `category_id` | `smallint` | Stable category identifier. |
| `parent_category_id` | `smallint` | The parent's `category_id`; `NULL` at a root. |
| `name` | `character varying(80)` | Unique category name. |

The hierarchy is a tree (RN-33): following `parent_category_id` upward from any
row ends at a root and never revisits a row.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type or
nullability, or changing the one-row-per-category rule is breaking. A breaking
revision is published as `api_categories.category_tree_v2` with a matching
`.v2.md` contract beside v1; v1 remains until recommendations has moved.

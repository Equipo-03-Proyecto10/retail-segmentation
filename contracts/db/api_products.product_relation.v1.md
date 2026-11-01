# `api_products.product_relation_v1`

`api_products.product_relation_v1` is a PostgreSQL view published by the
**products** boundary. It publishes the substitute and complement relations
between products, which products-service maintains through
`PUT /v1/products/{product_id}/relations/{relation_kind}` and serves through
`GET /v1/products/{product_id}/relations`. Consumers never receive privileges on
the products boundary's relation table.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `product_id` | `integer` | The product the relation is stated for. |
| `related_product_id` | `integer` | The related product. Never equal to `product_id`. |
| `relation_kind` | `text` | `substitute` or `complement`. |

A relation is directed: a row `(A, B, 'substitute')` does not imply a row
`(B, A, 'substitute')`. The view publishes the rows as stated and applies no
symmetry. It carries relations to products of any commercial status; a consumer
that must offer only sellable products joins `api_products.product_ref_v1` and
filters on `is_active`. The view uses the producer-owned source of truth and
does not copy relation state.

## Access and consumers

The consuming service is **recommendations**. It receives `USAGE` on
`api_products` and `SELECT` on this view only, and names the columns it reads.
The products boundary also holds `SELECT` as the producer. The view uses
PostgreSQL's definer-rights behavior (`security_invoker = off`), so those grants
do not expose the base table.

`contracts/db/grants.csv` is not changed by the contract story. Its rows are
added with the DDL, in the same pull request as the grants in
`sql/01_schema.sql`, because `check_grants.sh` compares the manifest with the
live database.

## Compatibility

Adding a column is non-breaking because consumers name their columns. Adding a
relation kind changes the promised meaning of `relation_kind` and is breaking.
Removing or renaming a consumed column, or changing its PostgreSQL type or
nullability, is breaking. A breaking revision is published as
`api_products.product_relation_v2` with
`contracts/db/api_products.product_relation.v2.md`; v1 remains until no
consumer has a grant on it.

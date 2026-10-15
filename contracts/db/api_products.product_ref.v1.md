# `api_products.product_ref_v1`

`api_products.product_ref_v1` is a PostgreSQL view published by the
**products** boundary. It is the stable product reference used across service
boundaries; consumers never receive privileges on `public.product`.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `product_id` | `integer` | Stable product identifier. |
| `sku` | `character varying(40)` | Unique stock-keeping unit. |
| `name` | `character varying(150)` | Product display name. |
| `category_id` | `smallint` | Identifier of the product's one category. |
| `list_price` | `numeric(10,2)` | Current non-negative list price. |
| `is_active` | `boolean` | Whether the product is available for current business operations. |

`image_path` is intentionally absent: it is web-managed and is not needed by
the cross-boundary rules. The view uses the producer-owned source of truth and
does not copy product state.

## Access and consumers

The consuming services are **sales**, **recommendations**, and **engagement**.
Each consumer receives `USAGE` on `api_products` and `SELECT` on this view only,
and names the columns it reads. The products boundary also holds `SELECT` as
the producer. The view uses PostgreSQL's definer-rights behavior
(`security_invoker = off`), so those grants do not expose the base table.

## Compatibility

Adding a column is non-breaking because consumers name their columns. Removing
or renaming a consumed column, changing its PostgreSQL type or nullability, or
changing its promised meaning is breaking. A breaking revision is published as
`api_products.product_ref_v2` with
`contracts/db/api_products.product_ref.v2.md`; v1 remains until no consumer has
a grant on it.

# Product Attribute Schema — `product.attributes`

**Story:** S0-04 — Frozen data model including segment traceability
**Owner:** Estefanía
**Version:** 1.0
**Date:** 2026-08-08
**Status:** Proposed — freezes with the data model at Sprint 0 Planning 2026-08-10
**Governs:** `product.attributes jsonb NOT NULL DEFAULT '{}'::jsonb`

---

## 1. What this document is

`001_m1_initial_schema.sql` carries this comment on the column:

> Typed attribute bag (material, size, colour, seasonality, dietary flags).
> Content-based recommendation (E-22) has no feature space without it, and adding
> it after 100k lines exist is a backfill exercise. Schema documented in
> `docs/data/product-attribute-schema.md`.

**This file is that schema.** It is the single definition of what may appear
inside `product.attributes`, and it exists because a JSONB column with no written
contract becomes a landfill within one sprint.

**Adding a key here is a documentation change, not a migration.** That is the
whole reason the attributes are JSONB rather than columns (D-13). Adding a key
requires: a row in the table in §3, an example in §4, and a pull request review.
It does not require an Alembic revision, does not lock the table, and does not
touch the 100,000+ transaction lines that reference these products. Removing or
renaming a key does require a data migration script, because documents already
written will still carry the old key — treat removal as expensive and addition
as cheap.

**Nothing in this file is enforced by the database.** The column is
`NOT NULL DEFAULT '{}'`, so an empty object is valid and every key is optional at
the storage level. "Required" in §3 means *required for content-based
recommendation to score this product*; a product missing those keys falls back to
category-only similarity, which works but ranks poorly. No `CHECK` constraint
validates the document, deliberately: a constraint would turn every schema
extension back into a migration and defeat D-13.

---

## 2. Why JSONB and not EAV or columns

Recorded in D-13 of `docs/data/postgresql-model.md` and restated here because it
is the question a reviewer asks first.

The read pattern is *"give me this product's attributes"* — never *"find all
products with attribute X across every product type"*. That is a document read,
not a relational query, so JSONB is the right shape and EAV would add a join and
a row per attribute for no benefit. Cross-product search, if it is ever needed,
is served by the GIN index already declared:

```sql
CREATE INDEX ix_product_attributes ON product USING gin (attributes);
```

Columns were rejected because the attribute set differs per category — dietary
flags are meaningless for homeware, material is meaningless for beverages — and a
wide table of mostly-NULL columns is the worst of both options.

---

## 3. Key definitions

Keys are `snake_case`. Values are JSON scalars or arrays of scalars; **no nested
objects**, so that a flattened feature vector for E-22 is a mechanical
transformation rather than a recursive one.

| Key | Type | Required for E-22 | Populated by | Notes |
|---|---|---|---|---|
| `price_tier` | string enum: `value`, `mainstream`, `premium` | yes | S0-08, derived from `unit_price` terciles within the category | The one attribute every product can carry, because it is computed rather than sourced |
| `seasonality` | string enum: `all_year`, `spring`, `summer`, `autumn`, `winter`, `holiday` | yes | S0-08 synthetic overlay | Drives recommendation relevance at the time of scoring; `all_year` is the safe default |
| `pack_size` | number | no | S0-08, parsed from the product name where a size is present | Numeric only; the unit lives in `pack_unit` |
| `pack_unit` | string enum: `ml`, `l`, `g`, `kg`, `unit`, `pack` | no | S0-08, parsed alongside `pack_size` | Present only when `pack_size` is present |
| `colour` | string | no | S0-08, parsed from the product name | Free string in M1; a controlled vocabulary is deferred until the seed shows how many distinct values actually occur |
| `material` | string | no | S0-08 synthetic overlay, apparel and homeware only | Free string, same reasoning as `colour` |
| `dietary` | array of string enum: `vegetarian`, `vegan`, `gluten_free`, `lactose_free`, `sugar_free`, `organic` | no | S0-08 synthetic overlay, food and beverage only | Empty array and absent key mean the same thing: unknown, not "contains everything" |
| `origin_country` | string, ISO 3166-1 alpha-2 | no | S0-08 synthetic overlay | Two upper-case letters |
| `is_promotional_line` | boolean | no | S0-08 synthetic overlay | Marks products that exist only as promotion vehicles; useful for excluding them from similarity |

### 3.1 Aspirational — not populated in M1

These are recorded so the key names are agreed before anyone invents their own,
but **no M1 code may depend on them** and the seed dataset will not contain them.

| Key | Type | Why it is deferred |
|---|---|---|
| `ingredients` | array of string | Not present in any public retail transaction dataset; would be fabricated |
| `allergens` | array of string | Same, and fabricating allergen data is irresponsible even in a course project |
| `size_chart` | string | Requires an apparel-specific taxonomy that M1 has no consumer for |
| `energy_kcal` | number | Nutrition data is a separate sourcing problem |
| `sustainability_score` | number | No defensible source; would be invented |

---

## 4. Example documents

Category codes are illustrative. **The category list is not frozen by this
document** — S0-08 derives it from the base retail dataset, so the codes below
are the ones the seed is expected to contain rather than a committed taxonomy.

**Beverages (`BEV`)**

```json
{
  "price_tier": "mainstream",
  "seasonality": "all_year",
  "pack_size": 600,
  "pack_unit": "ml",
  "dietary": ["vegetarian", "gluten_free"],
  "origin_country": "MX"
}
```

**Snacks (`SNK`)**

```json
{
  "price_tier": "value",
  "seasonality": "all_year",
  "pack_size": 45,
  "pack_unit": "g",
  "dietary": ["vegetarian"],
  "is_promotional_line": true
}
```

**Personal care (`PCR`)**

```json
{
  "price_tier": "premium",
  "seasonality": "all_year",
  "pack_size": 250,
  "pack_unit": "ml",
  "origin_country": "US"
}
```

**Homeware (`HOM`)**

```json
{
  "price_tier": "mainstream",
  "seasonality": "winter",
  "colour": "white",
  "material": "ceramic"
}
```

**Apparel (`APP`)**

```json
{
  "price_tier": "premium",
  "seasonality": "autumn",
  "colour": "navy",
  "material": "cotton",
  "origin_country": "PT"
}
```

**Minimum acceptable document.** A product that S0-08 could not enrich still
carries the two required keys:

```json
{ "price_tier": "mainstream", "seasonality": "all_year" }
```

---

## 5. Consumers

| Consumer | Uses | Milestone |
|---|---|---|
| E-22 Content-based recommendation | Whole document, flattened to a feature vector | M2 |
| E-03 Catalog screens | Display only; renders keys it recognises and ignores the rest | M1 |
| E-06 Segmentation | **Nothing.** Clustering features come from `customer_rfm_snapshot`, not from product attributes | M1 |

E-06 is listed to make the boundary explicit: product attributes are a
*recommendation* feature space, not a *segmentation* one. Category-mix behaviour
reaches the segmentation features through `customer_rfm_snapshot.top_category_id`
and `distinct_category_count` (D-06), never through this column.

---

## 6. Open questions

Both are recorded in `docs/scrum/assumption-register.md` rather than resolved
here.

1. Whether `colour` and `material` need controlled vocabularies depends on how
   many distinct values the seed produces. Decide after S0-08 loads, not before.
2. Whether `price_tier` should be recomputed when prices change, or frozen at
   ingestion like `sales_transaction_line.category_id` (D-12). M1 recomputes it,
   because no M1 feature reads it historically. If E-22 ever compares
   recommendations across time, this becomes the same problem D-12 solved.

---

## Revision history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-08-08 | Estefanía | Initial schema. Nine M1 keys, five deferred. Written to resolve the dangling reference in `COMMENT ON COLUMN product.attributes`. |

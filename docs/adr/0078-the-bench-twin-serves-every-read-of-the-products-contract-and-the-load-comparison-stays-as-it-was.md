# ADR-0078 — The bench twin serves every read of the products contract, and the load comparison stays as it was

**Status:** Accepted (2026-11-12)
**Owner:** Marcelo
**Issue:** #790
**Supersedes:** in part [ADR-0042](0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md) (which reads the twin serves, and what its database role may read)
**Superseded by:** —

---

## Context

ADR-0042 builds a Rust twin of the products reads, `bench/products-service-rs/`, so that
two languages can be compared doing the same work. It names the twin's reads as exactly
`GET /v1/products` and `GET /v1/products/{product_id}`, and its role `svc_products_bench` as
holding "SELECT on `public.product` and nothing else", with one row in `contracts/db/grants.csv`.

F21-10 (#790) implements `GET /v1/products/{product_id}/details` and
`GET /v1/products/{product_id}/relations` in products-service (contract 1.2.0, F21-09). Their
`x-mosaiq-status: planned` marks (ADR-0073) came off, so the shared conformance suite now tests
them against every build that serves the products contract, and fairness rule 1 of ADR-0042 says
that if either build fails it, nothing is measured. The twin answers 404 to both routes and so
fails that step, and with it the `Products twin` job of `bench.yml`.

ADR-0042 and the contract disagree about what "the read operations" means, and #924 left that
open. The reads are served from four tables of the products boundary (`products.product_status`,
`products.product_attribute`, `products.product_barcode`, `products.product_relation`), which
the role's one grant does not reach. Uncertain: whether a twin that serves more reads is still the
same experiment. It is, as long as nothing in the load changes, and this record says so.

## Decision

The twin serves every read operation of `contracts/products/openapi.yaml`: the two it served,
and now `getProductDetails` and `listProductRelations`, with the same JSON, the same XML under
`products.xsd`, the same status codes and the same error envelope as products-service. It still
implements no write: the four write operations (`createProduct`, `updateProduct`,
`updateProductDetails`, `replaceProductRelations`) are registered and answer 405. `svc_products_bench`
holds SELECT on `public.product` and on the four `products.*` tables, USAGE on the schema
`products`, and nothing else; each grant is a row of `contracts/db/grants.csv`. The load comparison
is unchanged: its four scenarios (list, search, filter, detail), the fairness rules and the report
`docs/evidence/products-reads-flask-vs-rust.md` stay as ADR-0042 and F15-03 left them, and the two
new reads are served to pass conformance, not benchmarked. Everything else of ADR-0042 stands: the
twin is never registered, routed, deployed, counted or pushed, runs only in the `bench` profile on
port 5103, and its crate follows the code rules of ADR-0045.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep the twin as it is and let the conformance step skip the two new reads for it | The skip would live in the harness, away from the contract and from review. ADR-0073 rejected the same idea for planned operations, and the twin's claim to serve "the same contract" would no longer be true |
| Mark the two reads `planned` again for the twin only | `planned` means "published ahead of an implementation". products-service has them, so the marker would be false for the contract that all builds share |
| Serve the two reads through the view `api_products.product_relation_v1` and a new view for details, so the role needs no table grant | A second pair of views would exist only for a benchmark, owned by the products boundary, and the details read has no consumer to name. The grants stay on the tables, read-only, like the one ADR-0042 already allowed |
| Add the two reads to the load comparison | It would change a measured experiment whose report (F15-03) is written and reviewed. A comparison of a read with joins and a nested list is a different question, and nobody asked it |
| Leave `bench.yml` failing on this one job | The job is not a required check, but a red job that is "expected" teaches the team to ignore it, and the next real failure hides behind it |

## Consequences

**What this makes easy.**
- Fairness rule 1 holds again: both builds pass the shared conformance suite and the products read
  cases, and answer a fixed sample identically, over all four reads.
- The twin follows the contract's reads, not a list copied into an ADR, so a later read needs no record.

**What this makes hard.**
- **The twin has two more handlers to keep.** They follow every change to the details and
  relations reads, and its tests compare them with products-service's answers.
- **The role now reaches five relations.** The bench database is throwaway and the role never
  exists on the instance (ADR-0042), but the grant manifest and ADR-0034's checks now list four more
  rows for it, and a new products table the twin must read is a change to this record, not a quiet grant.
- **The report is silent about the two reads.** A reader may assume the twin was measured on them.
  The report's threats to validity already name the scenarios, and `bench/README.md` says it.

**What must now be true elsewhere.**
- `sql/01_schema.sql` grants `svc_products_bench` USAGE on `products` and SELECT on the four tables,
  inside the products boundary section (idempotent), and `contracts/db/grants.csv` lists the five rows.
- `bench/products-service-rs/` registers the four details and relations operations; the two writes
  use the same 405 handler as `createProduct`. `bench/env/products-bench.env` sets
  `PRODUCTS_PHOTO_BASE_URL` to the value `bench/env/products.env` sets, so both builds answer the
  same `photo_url`.
- `docs/services/products.md` (E-22), `docs/services/README.md` and `bench/README.md` describe the
  twin as serving all four reads and benchmarking two.

## Compliance

ADR-0042's greps stand: the twin is never registered, routed, deployed or pushed, every bench
container is in the `bench` profile, and `grep -rnE '\.(post|put|patch|delete)\(' bench/products-service-rs/src/`
prints nothing. CI's `Products twin` job runs the Rust gates and the shared conformance suite
against the twin's image, which now includes the four operations. As the database owner, after
the three scripts, this query returns exactly the five relations below, and the second prints `f`:

```sql
SELECT c.oid::regclass AS relation
FROM pg_class c
WHERE c.relkind IN ('r', 'p', 'v', 'm')
  AND c.relnamespace NOT IN ('pg_catalog'::regnamespace, 'information_schema'::regnamespace)
  AND (has_table_privilege('svc_products_bench', c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')
       OR has_any_column_privilege('svc_products_bench', c.oid, 'SELECT, INSERT, UPDATE'))
ORDER BY relation::text;
-- product, products.product_attribute, products.product_barcode,
-- products.product_relation, products.product_status
SELECT bool_or(has_table_privilege('svc_products_bench', c.oid, 'INSERT, UPDATE, DELETE, TRUNCATE'))
FROM pg_class c
WHERE c.relkind = 'r' AND c.relnamespace IN ('public'::regnamespace, 'products'::regnamespace);  -- f
```

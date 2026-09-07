# ADR-0010 — The consultation module is a separate read-only blueprint, gated by two existing permissions

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #65 (F3-05)
**Supersedes:** —
**Superseded by:** —

---

## Context

F3-05 has to let a signed-in staff user *browse* the catalog, customers, stock
and segments (RF-10, RF-11, RF-13 / HU-10, HU-11). Two things about the state of
the code force a decision now.

**There is no read surface yet, only CRUD.** F3-04 (#64) built the catalog under
`/admin/*`, and every non-`CUSTOMER` role already holds `catalog.read` and can
open those listings — but the only per-record page is the admin edit form, gated
`catalog.write` and therefore ADMIN-only. A regular user who clicks a product
gets a 403. Reusing the `/admin/*` listings as the consultation surface would
mean showing every reader the New / Edit / Delete controls that refuse them.

**The permission matrix has no "Customers" column.** [`requirements.md`](../requirements.md)
§3 has columns for Catalogs, Users, Segments, Campaigns, Segment run, Reports and
Audit log. Customers are not one of them, and stock is only implied by
"Catalogs". F3-05 has to decide which permission gates the customer and stock
surfaces, and the honest answer is that it is a judgement call — a case could be
made for putting customers under `catalog.read` (the schema audits `customer`
alongside the catalog tables) or under a new `customer.read` permission.

**Row-level "own store" scoping is not buildable here.** §3 marks
STORE_MANAGER's Reports access as "own store", and the middleware's docstring
defers that narrowing to "the story that writes the query — F3-05". But
`app_user` has no `store_id` column (schema, `sql/01_schema.sql`): there is
nothing to scope by, and adding a column is a schema change outside this story.

## Decision

The consultation module is a new blueprint `catalog` at `/catalog`, read-only by
construction — every route is `GET` and calls only reading functions in
`web.db`. It does not reuse the `admin` blueprint. Its routes are gated by
permissions that already exist in `web/middleware/authz.py`: **products and
stock require `catalog.read`**; **customers and segments require `segment.read`**,
because customer data is the substrate of segmentation and RF-13 ("which
customers are in a segment") is segment data — this also keeps STORE_MANAGER and
INVENTORY_PLANNER, whose matrix rows concern stores and inventory rather than
people, out of the customer directory. No new permission name is introduced. The
`Catalogs` navigation entry is repointed from `admin.catalog_index` to
`catalog.index`; the administrator reaches the CRUD hub from a "Manage catalogs"
link on that page and the per-record "Edit" link on a detail page, both shown
only when the visitor holds `catalog.write`. Per-store filtering of the stock
view is a query parameter available to every `catalog.read` holder; no
per-user row scoping is attempted.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Add read-only detail routes to the existing `admin` blueprint and let the listings serve both audiences | The listings would still render write controls for readers, and the module F3-05 is graded on would have no identity of its own |
| Introduce a `customer.read` permission and a "Customers" matrix column | A new permission needs a seed-role review and an ADR-0007 amendment; `segment.read` already draws the line in the right place, and the roles that hold it are exactly HU-10's "analyst" and the audit/marketing roles |
| Gate customers under `catalog.read` | Would expose the full customer directory to STORE_MANAGER and INVENTORY_PLANNER, whose responsibilities are stores and stock, not people |
| Add `app_user.store_id` and scope STORE_MANAGER to its own store | A schema change outside this story; RF-11 asks only that "a user consults stock per store and product", not that a store manager is confined to one store |

## Consequences

**What this makes easy.** A regular user gets a clean browse experience with no
controls they cannot use. The module is one file (`web/routes/catalog.py`) plus
its templates and reads, mirroring `web/routes/audit.py`. Adding a section later
(campaigns, reports) is another route in the same blueprint.

**What this makes hard.** There are now two URLs that list products
(`/catalog/products` and `/admin/products`) and two that touch the same rows.
The split is deliberate — read vs. write — but a reviewer has to know which is
which. If a future story does need per-store scoping, `app_user` gains a column
and this ADR is superseded.

**What must now be true elsewhere.** [`requirements.md`](../requirements.md) §3
gains a sentence recording that customers and stock consultation are gated by
`segment.read` and `catalog.read` respectively, so the matrix and the code
agree. The `Catalogs` menu entry in `web/middleware/authz.py` points at
`catalog.index`; `test_authz.py`'s menu expectations depend on the label
staying `Catalogs`.

## Compliance

- `tests/test_authz.py::test_every_registered_endpoint_declares_what_it_requires`
  fails the build if any `catalog.*` view omits `@requires(...)`.
- `tests/test_negative_flows.py::test_refusal_matrix_covers_every_protected_route`
  enumerates every protected route, `/catalog/*` included.
- `tests/test_catalog.py` asserts an `INVENTORY_PLANNER` reaches `/catalog/stock`
  but not `/catalog/customers`, a `CUSTOMER` reaches nothing under `/catalog`,
  and an `ANALYST` is still refused `admin.edit_product_view`.

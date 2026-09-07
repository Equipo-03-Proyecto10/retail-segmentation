# F3-05 — The consultation module, read from the application

Evidence for RF-10, RF-11 and RF-13 (demonstration item *Consulta de
información*) and for HU-10 / HU-11. The `/catalog` blueprint is read-only by
construction: every route is `GET` and calls only reading functions in
`web.db`. Design and permission gating:
[ADR-0010](../adr/0010-the-consultation-module-is-a-separate-read-only-blueprint.md).

## How this run was produced

The application from this branch against the seeded Compose database — the same
three SQL scripts CI runs, no schema change:

```bash
docker compose up -d --build          # db seeds from sql/00,01,02; web on :8000
curl -c j -X POST localhost:8000/login \
     --data-urlencode email=user2@mosaiq-demo.com \
     --data-urlencode 'password=Password123!'     # user2 = ANALYST
```

Seed sizes exercised: `customer` 30, `inventory` 150 (30 stores × 5 products),
`segment` 30, `product` 40 (all `image_path` NULL).

## What each surface shows

### Products — list, search, detail (RF-10, HU-08 placeholder)

```
GET /catalog/products                     -> 200, "Page 1 of 2 · 40 products"
GET /catalog/products?q=Demo+Product+1     -> 200, "Page 1 of 1 · 11 products"
```

The search is `sku ILIKE %s OR name ILIKE %s`; `Demo Product 1` matches
`Demo Product 1` and `10`–`19`, hence 11. The pager links carry `q=` so the
search stays applied across pages.

```
GET /catalog/products/1  ->
    <dl class="facts">
      <dt>Product ID</dt>      <dd>1</dd>
      <dt>Category</dt>        <dd>Dairy</dd>
      <dt>List price</dt>      <dd>18.75</dd>
      <dt>Active</dt>          <dd>Yes</dd>
    </dl>
    <p class="placeholder">No image</p>
```

`category_id` is resolved to the category name in the route (`get_category`).
The seed carries no product images, so every detail page shows the placeholder
rather than a broken `<img>`; uploading one through the admin form (F3-07) makes
it appear here, served by the already-`catalog.read`-gated `admin.product_image`
route.

### Customers — list, search, detail (RF-10, HU-10)

```
GET /catalog/customers                       -> 200
GET /catalog/customers/00000000-…-000000000001 ->
    Demo Customer 1
    Registration channel : <name from channel>
    Current segment       : Segment 1   (links to /catalog/segments/1)
    Categories of interest : <names from customer_interest_category>
    Preferred channels     : <names from customer_preferred_channel>
```

`interests` and `preferred_channels` are the two 4NF multivalued tables joined
back to their catalogs. An unknown customer id (well-formed or not) is a 404.

### Stock — per store and product, filter, low-stock flag (RF-11, HU-11)

```
GET /catalog/stock              -> 200, all 150 rows, 20 to a page
GET /catalog/stock?store=1      -> 200, "Page 1 of 1 · 5 rows"
GET /catalog/stock?store=1&q=…  -> narrows further by product SKU/name
```

`inventory` is joined to `store` and `product` so each row names both. A row
whose `quantity_on_hand` is below `LOW_STOCK_THRESHOLD` (20) gets
`class="low-stock"` and a text `low` tag — it does not rely on colour alone
(RNF-11). `updated_at` is shown per row.

### Segments — list, detail, membership both directions (RF-13)

```
GET /catalog/segments        -> 200, 30 segments
GET /catalog/segments/1       ->
    Segment 1
    Rule            : RULE_001
    Recency band    : 1–3
    Frequency band  : 2–4
    Monetary band   : 3–5
    Customers in this segment: Demo Customer 1
```

The detail page answers "which customers are in this segment"
(`list_customers_in_segment`, paged); the customer detail page answers "which
segment is this customer in". An empty segment says so rather than showing an
empty table.

## Refusals — acceptance criterion 3

| Signed in as | Request | Result |
|---|---|---|
| `CUSTOMER` (user7) | `GET /catalog/products` | **403** |
| `CUSTOMER` | `GET /catalog/` | **403** |
| `INVENTORY_PLANNER` (user5) | `GET /catalog/stock` | 200 |
| `INVENTORY_PLANNER` | `GET /catalog/customers` | **403** — customers are `segment.read` |
| `STORE_MANAGER` (user3) | `GET /catalog/customers` | **403** |
| `ANALYST` (user2) | `GET /admin/products/1/edit` | **403** — the CRUD screen is `catalog.write` |
| `ANALYST` | `GET /catalog/products/99999` | **404** |
| `ANALYST` | `GET /catalog/segments/99999` | **404** |
| anonymous | `GET /catalog/products` | **302** → `/login?next=/catalog/products` |

In every 403 the refusal is by the gate, before the view runs, so no database
connection is opened — asserted in
`tests/test_negative_flows.py::test_customer_access`.

## Navigation

The `Catalogs` menu entry points at `catalog.index` (the read-only hub). It is
marked current on `/catalog/*` **and** on the admin CRUD pages under `/admin/`,
so an administrator editing a product still sees which section they are in. The
hub shows a **Manage catalogs** link (to `/admin/catalogs`) and each product
detail an **Edit** link only when the visitor holds `catalog.write`; the
customer and segment sections appear only with `segment.read`.

## Screenshots

`f3-05-*-375.png` and `f3-05-*-1440.png` — the product list, a product detail,
the stock view and a segment detail at both widths — are captured on a
workstation with a browser and attached to the pull request (RNF-11, DoD item
10, checked per PR). The pages use the same `table-scroll` / `filters` / `pager`
chrome as the audit view (F3-11), whose screenshots already show it holds at
375 px without horizontal overflow.

## Checks

- `pytest` — 498 passed, `tests/test_catalog.py` covering every surface, the
  refusal matrix, the search-as-parameter path and the empty states.
- `black --check .` and `ruff check .` — clean.
- `docker compose up` from a clean clone seeds and serves; the three SQL
  scripts run unchanged (no schema change in this story).

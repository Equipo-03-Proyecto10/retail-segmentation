# Store managers hold `segment.read`

Evidence for #280 and [ADR-0023](../adr/0023-store-managers-read-segments-and-customers.md).
F10-02 (#219) is written *as a store manager*, but `STORE_MANAGER` held no
`segment.read`, so the recommendations page refused the one role the story names.
The team decided on 2026-09-28 to grant it, accepting that `segment.read` opens the
whole customer directory and not only recommendations. This supersedes, in part,
ADR-0010's consequence that `segment.read` keeps store managers out of the directory.

The only code change is one line in `web/middleware/authz.py`:

```
"STORE_MANAGER": frozenset({CATALOG_READ, SEGMENT_READ, REPORT_READ}),
```

No schema change: the matrix is code (ADR-0007). No new permission, route or configuration.

## How this run was produced

PostgreSQL 16.2 loaded from empty with the three scripts, as in
[`catalog-label-reads.md`](catalog-label-reads.md): the local build has no `pg_trgm`,
so that one line was left out of `00`. One `RFM_RULES` run (run 31) was made through
the application's code. The application ran from this branch under gunicorn as the
restricted role `retail_app`. It was signed into through its own login form, as the
seeded `user15@…` (STORE_MANAGER) and `user17@…` (INVENTORY_PLANNER).

## What each role reaches

Status codes over HTTP, signed in:

| Request | STORE_MANAGER | INVENTORY_PLANNER |
|---|---|---|
| `GET /catalog/customers` | **200** | 403 |
| `GET /catalog/customers/<id>` | **200** | 403 |
| `GET /catalog/customers/<id>/profile` | **200** | 403 |
| `GET /catalog/customers/<id>/recommendations` | **200** | 403 |
| `GET /catalog/segments` | **200** | 403 |
| `GET /run-history/` | **200** | 403 |
| `GET /migration-matrix/` | **200** | 403 |
| `GET /migration-explanation/` | 404 (see below) | 403 |
| `GET /model-comparison/` | **200** | 403 |
| `GET /segment-run/` | 403 | 403 |
| `POST /segment-run/` | 403 | 403 |
| `GET /campaigns/` | 403 | 403 |
| `GET /admin/users` | 403 | 403 |
| `GET /audit/` | 403 | 403 |

The store manager reaches every surface gated on `segment.read`, and nothing it could
write with: running a segmentation, campaigns, users and the audit log are still
refused. The inventory planner is still refused the whole customer and segment surface.

`/migration-explanation/` answers 404 to the store manager because the gate let the
request through and the page had nothing to explain: there is only one run. A signed-in
ANALYST gets the same 404 on the same database. The inventory planner's 403 on it is
the gate.

## The recommendations page, as the store manager

Signed in as *Demo User 15, STORE_MANAGER*. The page shows Demo Customer 1's three
recommendations from Store 8's stock, each with its reasons.

| | |
|---|---|
| 1440 px | [`store-manager-recommendations-1440.png`](store-manager-recommendations-1440.png) |
| 375 px | [`store-manager-recommendations-375.png`](store-manager-recommendations-375.png) |
| The customer list it links back to | [`store-manager-customers-1440.png`](store-manager-customers-1440.png) · [375 px](store-manager-customers-375.png) |
| The segment run, still refused | [`store-manager-segment-run-refused-1440.png`](store-manager-segment-run-refused-1440.png) |

The role's menu, read from the page:

```
Home · Catalogs · Segments · Run history · Migration matrix · Model comparison · Reports
```

Horizontal overflow of the page body (`scrollWidth - clientWidth`) was **0 px** for the
recommendations page and the customer list at 375 px and 1440 px.

## Tests

* `tests/test_authz.py` pins the store manager's exact set (`catalog.read`,
  `segment.read`, `report.read`), and that the inventory planner holds no
  `segment.read`. It also pins the store manager's menu.
* The route tests for the consumption profile, recommendations and model comparison
  list STORE_MANAGER among the roles served.
* Those for the migration matrix and explanation and run history no longer list it
  among the refused. INVENTORY_PLANNER and CUSTOMER remain refused on all six.
* The catalog hub test for a catalog-only role now signs in as INVENTORY_PLANNER.

With `SEGMENT_READ` taken away from STORE_MANAGER again, five tests fail.

```
$ pytest -q
1411 passed
$ black --check .   # 113 files would be left unchanged
$ ruff check .      # All checks passed!
```

## Earlier evidence

The refusals captured for this role in
[`f8-04-refused-store-manager-1440.png`](f8-04-refused-store-manager-1440.png),
[`f9-04-refused-store-manager-1440.png`](f9-04-refused-store-manager-1440.png) and
[`f10-02-refused-store-manager-1440.png`](f10-02-refused-store-manager-1440.png)
record those pages as they were merged. They are kept, and no longer describe the
current matrix.

# QA findings #361 and #362 — documentation and inventory decision

## Result

The requirements now describe the delivered analytics phase rather than the
first-delivery deferral. Integrity cases N13, N14 and N16 name PostgreSQL's
`23001 restrict_violation`. The permission map distinguishes the
`report.read` revenue summary from the revenue-by-label chart gated by
`segment.read`, and accounts for every permission that currently has no route.

ADR-0031 records the inventory spike's outcome: accepted sales decrement the
matching stock row atomically. Missing or insufficient stock rejects the row
and rolls back its header, line and stock change. A separate, auditable
inventory entry point remains sequenced in the roadmap for non-sale receipts,
transfers, counts and adjustments.

## Verification

```bash
grep -n "23001 restrict_violation" sql/verify_integrity.sql
grep -n "/admin/sales-import/" docs/analytics-permission-map.md
grep -n "Declared permissions without a route" docs/analytics-permission-map.md
grep -n "Accepted sales decrement inventory" \
  docs/adr/0031-sales-ingestion-decrements-inventory-atomically.md
grep -n "decrement_stock" web/services/ingestion.py web/db/inventory.py
pytest -q tests/test_ingestion_service.py tests/test_inventory_db.py
! grep -n "What it does not promise" docs/requirements.md
```

These checks cover the documentation decision and the production inventory
write. With the combined seven-issue change set, `.venv/bin/pytest -q` passed
all 2948 tests, `black --check .` left all 191 files unchanged, and `ruff
check .` passed. The three SQL scripts loaded in order into an isolated empty
PostgreSQL 18.6 cluster; no schema change was needed for this decision.

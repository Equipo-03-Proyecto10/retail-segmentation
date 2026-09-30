# ADR-0031 — Accepted sales decrement inventory atomically

**Status:** Proposed
**Owner:** Team 03
**Issue:** #362
**Supersedes:** —
**Superseded by:** —

---

## Context

`inventory.quantity_on_hand` is the authoritative on-hand quantity used by
recommendations and the stock consultation. An accepted sale has the exact
store, product and quantity that it consumed. The issue discussion therefore
settles the previously open question: an accepted sale must consume that
stock automatically.

The operation must remain safe when imports overlap. A missing inventory row or
too little stock must reject the row without leaving a transaction header,
line, or partial stock decrement behind. A duplicate line for an already
accepted transaction is rejected before it can consume stock a second time.

## Decision

Sales ingestion decrements the matching inventory row in the same database
transaction as the transaction header and line. It uses a parameterized,
conditional `UPDATE` guarded by `quantity_on_hand >= quantity`; if no row is
updated, a `SELECT ... FOR UPDATE` distinguishes a missing row from
insufficient stock and the service reports a typed row rejection. The service
transaction rolls back the header, line and any stock change on that rejection.

The existing `inventory.write` permission remains reserved for a future
server-rendered receipt, transfer, count, or adjustment entry point. That
entry point will handle non-sale movements and will not be a second mechanism
for recording a sale. No new service, API, or deployable unit is introduced.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Do not decrement on accepted sales | Contradicts the issue's final product direction and leaves stock permanently overstated. |
| Decrement after the sale transaction commits | A crash between the two writes would create a sale without its stock movement. |
| Unconditional decrement | Could make stock negative; the database check alone is not enough to provide a useful row-level rejection or rollback boundary. |
| Use only a separate inventory entry point | Cannot atomically link the stock movement to the accepted sale. |

## Consequences

**What this makes easy.** Every accepted imported line consumes stock at its
store and product. Stock cannot be consumed by a rejected or duplicate line,
and concurrent sales are serialized by PostgreSQL's row lock and conditional
update.

**What this makes hard.** A late or replayed accepted sale consumes stock under
the same rule, and a missing/insufficient inventory row requires the import
row to be refused rather than creating a negative balance.

**What must now be true elsewhere.** Existing consultation and recommendation
reads continue to use the same inventory table. Non-sale movements remain
future work under `inventory.write`.

## Compliance

* `web/services/ingestion.py` calls `web.db.inventory.decrement_stock` inside
  the ADR-0020 service transaction.
* `tests/test_ingestion_service.py` proves accepted decrement and rollback for
  missing or insufficient stock and for a failure after decrement;
  `tests/test_inventory_db.py` checks the parameterized conditional update and
  row lock.
* The operation uses no schema change; `sql/01_schema.sql` remains the source
  of the inventory table definition.

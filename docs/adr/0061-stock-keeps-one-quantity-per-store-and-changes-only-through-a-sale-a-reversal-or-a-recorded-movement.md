# ADR-0061 — Stock keeps one quantity per store, and changes only through a sale, a reversal or a recorded movement

**Status:** Accepted (2026-10-31)
**Owner:** Marcelo
**Issue:** #771
**Supersedes:** in part, [ADR-0031](0031-sales-ingestion-decrements-inventory-atomically.md) (the server-rendered entry point it reserved for non-sale movements) and [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (one synchronous cross-boundary call)
**Superseded by:** —

---

## Context

The instructor's catalog asks stock-service for stock by store and channel,
availability, reservations where they apply, movements and updates, and for control
so that exhausted products are not recommended.

Today `inventory` is keyed by store and product, and changes only when a sale
consumes it. stock-service only reads, and its one write path, `api_stock.consume_v1`,
runs inside the sale. `inventory.write` has no route: ADR-0031 reserved a
server-rendered entry point for receipts, transfers, counts and adjustments, and left
its reason, idempotency and concurrency rules undecided. Without one, nothing can
restock a product, so no test can show a recommendation returning when stock does.

Two calls are judgement. "Stock by channel" could mean a quantity per channel, and
this record reads it as availability per channel, because a store holds one physical
pile. And the brief says reservations "when applicable", which this record reads as
"not until something needs them".

## Decision

`inventory` keeps one on-hand quantity per store and product. Availability is derived,
per channel: a product is available in a store and channel when its commercial status
allows the sale, the store enables that channel, and its on-hand quantity is above
zero. Beyond a sale, stock changes only through a recorded movement or a sale's
reversal, both through stock-service's boundary, and on-hand is never negative.

| Movement | Effect on on-hand | Must state |
|---|---|---|
| `RECEIPT` | Adds a positive quantity | Reference, such as a delivery note |
| `TRANSFER` | Two rows sharing a `transfer_id`: subtracts at the origin, adds at the destination, in one transaction | Origin and destination stores |
| `COUNT` | Sets on-hand to the counted quantity, and stores before and after | Who counted |
| `ADJUSTMENT` | Adds or subtracts | A reason from `DAMAGE`, `LOSS`, `EXPIRY` or `CORRECTION` |
| `RESTOCK` | Adds the returned units | The sale reversal, through `api_stock.restock_v1` |

- **Storage.** `stock.stock_movement` is append-only, in a new `stock` schema (ADR-0046). `definer_stock` applies a movement and its `inventory` change in one transaction.
- **Idempotency.** `POST /v1/stock-movements` requires an `Idempotency-Key`. A replay returns the first result, and the same key with another body is 409, as in sales.
- **Concurrency.** Quantities change by a conditional `UPDATE`, as `consume_v1` does, so two movements on one row add up. A transfer locks its two rows in ascending store order, so two opposite transfers cannot deadlock.
- **Roles.** `ADMIN` and `INVENTORY_PLANNER`, which writes for the first time. A sale does not create a movement: `transaction_line` is already its record, so on-hand reconciles as opening stock plus movements minus net units sold.
- **Reservations are not adopted.** A sale is atomic, and an offline draft is validated when it returns (F27-13), so nothing needs to hold stock. F21-08 closes as not planned. A reservation would apply if a channel ever had to hold a cart beyond the sale's transaction.
- **A second synchronous write.** `api_stock.restock_v1` is called by sales-service inside the reversal's transaction, for the reason `consume_v1` is: the reversal and its stock must commit together. E-05 is widened to name both, with the same guard.

The registry's rule becomes: stock is never negative, and changes only through an
accepted sale, its reversal or a recorded movement with a reason (RN-14, RN-69).

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| A quantity per channel, keyed by store, channel and product | It changes the key of a table the web and `consume_v1` read, and needs allocation rules for one physical pile that nobody asked for |
| One ledger holding every change, sales included | It would have to catch the web's `decrement_stock`, and `retail_app` cannot reach `stock.*`. A trigger could, but `transaction_line` already records each sale, and reconciliation gives the same assurance |
| The server-rendered entry point ADR-0031 reserved | The planner's console is Electron, which reaches only services over XML. The web keeps `inventory.write` declared with no route |
| Reservations now | They add expiry, consumption and a second availability figure, for a case the atomic sale and the offline validation already cover |

## Consequences

**What this makes easy.** A stock change has a reason and an author. F24-08 can show a
recommendation disappear and return. The planner gains a real write.

**What this makes hard.** stock-service becomes a writer, so ADR-0043's table no longer
lists it as read rule only. A reader who expects a quantity per channel has to be told
what availability means; `docs/scope-delivery-3.md` records it, and the instructor may
ask for more.

**What must now be true elsewhere.**
- F21-05 publishes the contract and `api_stock.restock_v1`. F21-06 and F21-07 implement them, and E-05 and the stock sheet are updated there.
- `stock.v1.changed` needs no change: its trigger on `inventory` fires for every writer.
- `docs/roadmap.md` stops listing `inventory.write` as undecided, and `docs/requirements.md` §3 stops saying no operation lists `INVENTORY_PLANNER`.
- `web/` keeps no stock write beyond the sale.

## Compliance

`contracts/db/check_grants.sh` compares the new grants both ways, and `restock_v1` goes
to `svc_sales` only. A concurrency test with real connections proves two movements add
up and that opposite transfers finish. A reconciliation query over the seed proves
on-hand equals opening stock plus movements minus net units sold.

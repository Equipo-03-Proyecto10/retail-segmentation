# ADR-0062 — A reversal is a recorded fact, and every reader counts net units

**Status:** Accepted (2026-10-31)
**Owner:** Marcelo
**Issue:** #769
**Supersedes:** in part, [ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md) (a sale as the only sales write) and [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (one synchronous cross-boundary call)
**Superseded by:** —

---

## Context

The instructor's catalog gives sales-service returns and cancellations, and asks that
RFM be verified over cancelled and returned sales. The retrospective asks how late,
duplicate and voided sales are treated.

No reversal exists today. `transaction.total` is never negative, a line's quantity is
always positive, and RN-09 only describes a delete, which no route performs.

Where a reversal lives is forced by one constraint. ADR-0046 gives `retail_app` USAGE
on `public` alone, and CI runs an assertion that fails if it can reach anything else.
So a reversal held only in `sales.*` would be invisible to the web, whose reports
would keep counting the sale, and neither could the web read an `api_sales` view. The
web reads sales in 38 statements across 12 files, and the instructor asks for parity.

The judgement calls are the time limits, which are configuration defaults and not
requirements, and storing the returned units, which repeats what the reversal rows
add up to. It is the same shape as `inventory` and its movements: a current state next
to its history.

## Decision

A reversal is an append-only fact that never edits or deletes a sale. Its detail lives
in `sales.sale_reversal` and `sales.sale_reversal_line`, and each line's running total
lives in a new column, `transaction_line.returned_quantity`, which sales-service alone
writes, in the same transaction. Every reader of accepted sales counts net units:
`quantity - returned_quantity`.

| | Cancellation | Return |
|---|---|---|
| Scope | The whole sale | One or more lines, whole or in part |
| When (defaults, in configuration) | The sale's calendar day | Within 30 days of `occurred_at` |
| Who | `SALES_ASSOCIATE`, `STORE_MANAGER`, `ADMIN`, in their stores (ADR-0060) | `STORE_MANAGER`, `ADMIN`, in their stores |
| Stock | Every unit restored through `api_stock.restock_v1` | Restored unless the line says the goods cannot be resold |
| Must state | A reason | A reason per line |

`POST /v1/sale-reversals` requires an `Idempotency-Key`, and returning more units than
remain is 409. `returned_quantity` carries `CHECK (returned_quantity BETWEEN 0 AND
quantity)`. A trigger refuses any writer but `svc_sales`, as E-16's triggers do. A
reversal is announced on `stream:sales.v1.reversed` from the outbox, in its transaction.

**What net means.** A sale's net amount is the sum of `(quantity - returned_quantity) *
unit_price`. A sale whose net amount is zero stops counting as a purchase.

| Reader | Effect of a reversal |
|---|---|
| R, F, M | M is the sum of net amounts. F and R count only sales with a net amount above zero, so a whole cancellation can move a customer's most recent purchase, and a partial return changes M alone. With no such sale in the window, the customer is unassigned (RN-21) |
| Revenue and reports | Net. `transaction.total` stays what was charged |
| Stock | As the table above |
| Conversion and attribution | F25-02 decides, and conversions stay append-only (ADR-0050) |

**How the web reads it.** Two read-only views in `public`, `net_sale_line` and
`net_sale`, hold that one definition, and the web's 36 statements over accepted sales
move to them: `consumption` (10), `experiment_conversions` (6), `consumption_shift`
(6), `revenue_summary` (4), `segments`, `recommendations` and `customer_timeline`
(2 each), and `segmentation_dashboard`, `experiments`, `experiment_report` and
`experiment_dashboard` (1 each). `web/db/sales.py`'s two statements stay gross, since
they validate ingestion. `api_sales` publishes the same definition for services, and
`api_sales.purchase_line_v1` keeps its meaning. The web gains no route, no Redis and no
service call.

**Late sales.** A sale counts by its `occurred_at`, never by when it was recorded,
subject to RN-37. A late sale changes the customer's current profile and never rewrites
a closed run (ADR-0017).

**Duplicates.** A repeated `Idempotency-Key` or `source_transaction_id` records nothing
new: the service replays the first result, the same key with another body is 409, and
a CSV row is rejected with its reason. A redelivered event is dropped by its
`event_id`. Two sales that only look alike are not duplicates: F26-09 lists them for a
person to settle.

**Voided sales** are cancellations. The system has no third state.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep the reversal only in `sales.*` | `retail_app` cannot read it, so the web's reports would count reversed sales and parity would break |
| Grant `retail_app` an `api_sales` view | CI fails if the web reaches any schema but `public`, which is what ADR-0046 exists to guarantee |
| A new reversal table in `public` | No `svc_*` role may hold a privilege on a `public` table outside the legacy set (ADR-0046) |
| A compensating negative sale | It breaks `total >= 0` and `quantity > 0`, and would count as a second purchase in F |
| Edit or delete the sale | It destroys the evidence, and nothing could say what was bought or who reversed it |

## Consequences

**What this makes easy.** One definition of net serves the web and the services, so
they cannot disagree on it. A cancelled sale can be followed through stock, RFM and
reports.

**What this makes hard.** The web changes in 36 statements, which is the largest change
it has had since the freeze, and its golden fixtures are captured again.
`returned_quantity` repeats the sum of the reversal rows, so `docs/data-model.md` must
justify it, and a check must prove they match. A reader that goes back to `transaction`
directly would silently count gross; a test has to catch it.

**What must now be true elsewhere.**
- F21-02 publishes the contract and the stream. F21-03 implements them, adds the column and the views, and moves the web's statements.
- `docs/business-rules.md` gains RN-70. RN-09 stays, as a statement about a cascade no route uses.
- E-05 names a second synchronous call, as ADR-0061 records.
- F22-06 and F29-01 test the table above.

## Compliance

A CI query proves `returned_quantity` equals the sum of the reversal lines for every
sale in the seed. `tests/parity/` covers a reversal through the web's reports and
`GET /v1/stock-levels`. The `APPLICATION ROLE VERIFICATION` block keeps proving the web
reaches only `public`.

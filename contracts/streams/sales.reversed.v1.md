# `stream:sales.v1.reversed`

## Contract

| Field | Value |
|---|---|
| Stream | `stream:sales.v1.reversed` |
| Producer | sales-service |
| Captured table | `sales.sale_reversal` |
| Captured operations | `INSERT` only. The table is append-only, and a trigger refuses any writer but `svc_sales`, so no `UPDATE` or `DELETE` exists to capture |
| Outbox | `sales_events.outbox` |
| Private wake-up channel | `sales_outbox` |
| Consumer groups | `recommendations`, `rfm-engine`, `experimentation` |
| Retention | `XADD MAXLEN ~ 10000` |
| Delivery | At least once |

A reversal is a cancellation or a return of an accepted sale (ADR-0062). An AFTER
INSERT row trigger on `sales.sale_reversal` writes one outbox row in the same
transaction as the reversal, its lines, the `returned_quantity` updates and the
stock restored through `api_stock.restock_v1`. A rollback therefore publishes
nothing, and a committed reversal cannot go unannounced. The request path never
runs `XADD`: the owner's relay does, from the outbox.

The stream is separate from `stream:sales.v1.recorded`. A reversal never updates
`transaction.total`, so it raises no event there, and a consumer of recorded sales
cannot learn of a reversal from it.

## Entry fields

Every entry is key-only and carries no sale, reason, quantity or amount:

| Field | Encoding and meaning |
|---|---|
| `event_id` | UUID string generated for the outbox row; the deduplication key. |
| `event_type` | The literal `INSERT`. |
| `entity_id` | `sale_id:reversal_id`, both base-10 text. The `sale_id` is the `transaction_id` of the reversed sale. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted as UTC with `Z`. It is when the reversal was recorded, not when the sale happened. |
| `v` | The literal `1`. |

## How many events a reversal raises

Exactly one per reversal, whichever kind. A sale with a return, then another
return, then no more, raises two events with the same `sale_id` and different
`reversal_id`. A replayed `POST /v1/sale-reversals` (same `Idempotency-Key`, same
body) records nothing and raises nothing. A consumer must still be idempotent,
because delivery is at least once.

## Reading the state

State is never read from the event. A consumer parses `sale_id` from `entity_id`
and reads the database contracts:

- the net purchases of the sale from `api_sales.net_purchase_line_v1`, which
  holds the lines that still count; and
- the customer and store of the sale from `api_sales.purchase_line_v1`, which is
  gross and keeps its meaning. A sale cancelled in full has no row in the net
  view, so this is how a consumer finds the customer it must act for.

The reversal's reason and lines are read through `GET /v1/sale-reversals` by a
client, never by another service (services never call each other over HTTP).

## Consumer behaviour

| Group | What it does with an entry |
|---|---|
| `recommendations` | Invalidates its recommendation cache for the customer, since a returned product is bought no more and may be recommended again. |
| `rfm-engine` | Recomputes R, F and M for that customer only (RN-65). A whole cancellation can move the most recent purchase; a partial return changes M alone. |
| `experimentation` | Re-evaluates conversions of that customer's sale in their window, net of the reversal. Conversions stay append-only (ADR-0050). |

Each group deduplicates by `event_id` before acknowledging an entry. On restart a
consumer handles its pending entries first, may claim entries idle for more than
60 seconds, and acknowledges only after its work succeeds. After five failed
deliveries it logs and acknowledges the entry; the outbox row remains in the
seven-day replay log.

A group is created by its service's first deploy. The groups `rfm-engine` and
`experimentation` are declared here so that those services build against a fixed
stream; they are registered in `contracts/registry.yaml` by their own stories.

## Failure and compatibility

The owner relay waits on `sales_outbox` only as a private wake-up and also polls at
least every five seconds. It relays unrelayed rows in order in batches of at most
100 and marks them relayed only after Redis accepts the entry. A crash can
duplicate an entry but cannot lose a committed outbox row.

Changing a field's meaning or encoding, removing a field, changing how `entity_id`
identifies the reversal, or adding state to the payload is breaking. A breaking
revision creates `stream:sales.v2.reversed` and a matching `.v2.md` contract;
capture writes both versions until every consumer group has moved.

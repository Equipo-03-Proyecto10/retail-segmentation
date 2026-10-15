# `stream:stock.v1.changed`

## Contract

| Field | Value |
|---|---|
| Stream | `stream:stock.v1.changed` |
| Producer | stock-service |
| Captured table | `public.inventory` |
| Captured operations | `INSERT`, `UPDATE`, `DELETE`, by any writer |
| Outbox | `stock_events.outbox` |
| Private wake-up channel | `stock_outbox` |
| Consumer group | `recommendations` |
| Retention | `XADD MAXLEN ~ 10000` |
| Delivery | At least once |

The capture trigger runs for every writer: the web's CSV decrement,
`api_stock.consume_v1`, and anything else that changes `inventory`. It inserts
into the stock outbox in the same transaction as the change, so a rollback
publishes nothing.

## Entry fields

Every entry is key-only and carries no stock figure.

| Field | Encoding and meaning |
|---|---|
| `event_id` | UUID string generated for the outbox row; the deduplication key. |
| `event_type` | The trigger's `TG_OP`: `INSERT`, `UPDATE` or `DELETE`. |
| `entity_id` | `store_id:product_id`, both base-10 text; for a delete it comes from the deleted row. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted as UTC with `Z`. |
| `v` | The literal `1`. |

The `recommendations` group invalidates its result cache. It deduplicates by
`event_id` before acknowledging an entry. Current state is re-read from
`api_stock.stock_level_v1`, never from the event.

## Failure and compatibility

The owner relay waits on `stock_outbox` only as a private wake-up and also
polls at least every five seconds. It relays unrelayed rows in order and marks
them relayed only after Redis accepts the entry, so a crash can duplicate an
entry but cannot lose a committed outbox row.

Changing a field's meaning or encoding, removing a field, changing how
`entity_id` identifies the pair, or adding state to the payload is breaking. A
breaking revision creates `stream:stock.v2.changed` and a matching `.v2.md`
contract; capture writes both versions until the `recommendations` group has
moved.

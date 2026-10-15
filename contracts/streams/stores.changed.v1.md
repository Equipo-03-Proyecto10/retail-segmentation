# `stream:stores.v1.changed`

## Contract

| Field | Value |
|---|---|
| Stream | `stream:stores.v1.changed` |
| Producer | stores-service |
| Captured tables | `public.store`, `stores.store_location` |
| Captured operations | `INSERT`, `UPDATE`, `DELETE` on either table, by any writer |
| Outbox | `stores_events.outbox` |
| Private wake-up channel | `stores_outbox` |
| Consumer group | `stores` |
| Retention | `XADD MAXLEN ~ 10000` |
| Delivery | At least once |

The capture trigger runs for every writer, including the frozen web's writes
to `public.store`. It inserts into the stores transactional outbox in the same
transaction as the change. A rollback therefore publishes nothing.

## Entry fields

Every entry is key-only and carries no store state:

For both captured tables, `entity_id` = `store_id`.

| Field | Encoding and meaning |
|---|---|
| `event_id` | UUID string generated for the outbox row; the deduplication key. |
| `event_type` | One of `INSERT`, `UPDATE`, or `DELETE`. |
| `entity_id` | Base-10 text form of `store_id`; for a delete it comes from the deleted row. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted as UTC with `Z`. |
| `v` | The literal `1`. |

The `stores` consumer group invalidates stores-service's own directory cache.
It deduplicates by `event_id` before acknowledging an entry. On restart it
handles pending entries first, may claim entries idle for more than 60 seconds,
and acknowledges only after invalidation succeeds. After five failed
deliveries it logs and acknowledges the entry; the outbox row remains in the
seven-day replay log. Current state is re-read from
`api_stores.store_directory_v1`, never from the event.

## Failure and compatibility

The owner relay waits on `stores_outbox` only as a private wake-up and also
polls at least every five seconds. It relays unrelayed rows in order in batches
of at most 100 and marks them relayed only after Redis accepts the entry. A
crash can therefore duplicate an entry but cannot silently lose the committed
outbox row.

Changing a field's meaning or encoding, removing a field, changing how
`entity_id` identifies the store, or adding state to the payload is breaking.
A breaking revision creates `stream:stores.v2.changed` and a matching `.v2.md`
contract; capture writes both versions until the `stores` group has moved.

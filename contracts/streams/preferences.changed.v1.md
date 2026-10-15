# `stream:preferences.v1.changed`

## Contract

| Field | Value |
|---|---|
| Stream | `stream:preferences.v1.changed` |
| Producer | customer-preferences-service |
| Captured tables | `public.customer_preferred_channel`, `public.customer_interest_category` |
| Captured operations | `INSERT` and `DELETE` on either table, by any writer |
| Outbox | `preferences_events.outbox` |
| Private wake-up channel | `preferences_outbox` |
| Consumer group | `recommendations` |
| Retention | `XADD MAXLEN ~ 10000` |
| Delivery | At least once |

The capture trigger runs for every writer and inserts into the preferences
transactional outbox in the same transaction as the change, so a rollback
publishes nothing. A whole-set replace is a DELETE and INSERTs, so it raises
several events for one customer.

## Entry fields

Every entry is key-only and carries no preference state:

For both captured tables, `entity_id` = `customer_id`.

| Field | Encoding and meaning |
|---|---|
| `event_id` | UUID string generated for the outbox row; the deduplication key. |
| `event_type` | `INSERT` or `DELETE`. |
| `entity_id` | The customer's UUID in its canonical lowercase text form. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted as UTC with `Z`. |
| `v` | The literal `1`. |

The `recommendations` group invalidates its cache for that customer. It
deduplicates by `event_id` before acknowledging an entry, so the several events
of one replace cost one recomputation at most. On restart it handles pending
entries first, may claim entries idle for more than 60 seconds, and
acknowledges only after invalidation succeeds. After five failed deliveries it
logs and acknowledges the entry; the outbox row remains in the seven-day replay
log. Current state is re-read from `api_preferences.interest_category_v1` and
the preferences contract, never from the event.

## Failure and compatibility

The owner relay waits on `preferences_outbox` only as a private wake-up and
also polls at least every five seconds. It relays unrelayed rows in order in
batches of at most 100 and marks them relayed only after Redis accepts the
entry. A crash can duplicate an entry but cannot lose a committed outbox row.

Changing a field's meaning or encoding, removing a field, changing how
`entity_id` identifies the customer, or adding state to the payload is
breaking. A breaking revision creates `stream:preferences.v2.changed` and a
matching `.v2.md` contract; capture writes both versions until the
`recommendations` group has moved.

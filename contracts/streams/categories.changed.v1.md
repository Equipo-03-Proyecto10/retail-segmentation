# `stream:categories.v1.changed`

## Contract

| Field | Value |
|---|---|
| Stream | `stream:categories.v1.changed` |
| Producer | categories-service |
| Captured table | `public.category` |
| Captured operations | `INSERT`, `UPDATE`, `DELETE`, by any writer |
| Outbox | `categories_events.outbox` |
| Private wake-up channel | `categories_outbox` |
| Consumer groups | `categories`, `recommendations` |
| Retention | `XADD MAXLEN ~ 10000` |
| Delivery | At least once |

The capture trigger runs for every writer, including the frozen web's edits and
hard deletes. It inserts into the categories transactional outbox in the same
transaction as the change, so a rollback publishes nothing.

## Entry fields

Every entry is key-only and carries no category state:

| Field | Encoding and meaning |
|---|---|
| `event_id` | UUID string generated for the outbox row; the deduplication key. |
| `event_type` | One of `INSERT`, `UPDATE` or `DELETE`. |
| `entity_id` | Base-10 text form of `category_id`; for a delete it comes from the deleted row. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted as UTC with `Z`. |
| `v` | The literal `1`. |

## Consumers

| Group | Effect |
|---|---|
| `categories` | Invalidates categories-service's own tree cache. |
| `recommendations` | Invalidates recommendations' cache of category-based signals. |

Each group deduplicates by `event_id` before acknowledging an entry. On restart
it handles pending entries first, may claim entries idle for more than 60
seconds, and acknowledges only after its effect succeeds. After five failed
deliveries it logs and acknowledges the entry; the outbox row remains in the
seven-day replay log. Current state is re-read from
`api_categories.category_tree_v1`, never from the event.

## Failure and compatibility

The owner relay waits on `categories_outbox` only as a private wake-up and also
polls at least every five seconds. It relays unrelayed rows in order in batches
of at most 100 and marks them relayed only after Redis accepts the entry. A
crash can duplicate an entry but cannot lose a committed outbox row.

Changing a field's meaning or encoding, removing a field, changing how
`entity_id` identifies the category, or adding state to the payload is
breaking. A breaking revision creates `stream:categories.v2.changed` and a
matching `.v2.md` contract; capture writes both versions until both groups
have moved.

# `stream:sales.v1.recorded`

## Contract

| Field | Value |
|---|---|
| Stream | `stream:sales.v1.recorded` |
| Producer | sales-service |
| Captured table | `public.transaction` |
| Captured operations | `INSERT` and `UPDATE OF total`, by any writer: the service and the CSV path |
| Outbox | `sales_events.outbox` |
| Private wake-up channel | `sales_outbox` |
| Consumer groups | `recommendations`, `rfm-engine` |
| Retention | `XADD MAXLEN ~ 10000` |
| Delivery | At least once |

An AFTER INSERT OR UPDATE OF `total` row trigger on `transaction` writes one
outbox row in the same transaction as the change. A rollback therefore
publishes nothing. The trigger fires for the frozen web's CSV path too, so no
writer is invisible.

## Entry fields

Every entry is key-only and carries no sale state:

| Field | Encoding and meaning |
|---|---|
| `event_id` | UUID string generated for the outbox row; the deduplication key. |
| `event_type` | `INSERT` or `UPDATE`. |
| `entity_id` | Base-10 text form of `transaction_id`. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted as UTC with `Z`. |
| `v` | The literal `1`. |

## How many events a sale raises

| Path | Events |
|---|---|
| A sale through `POST /v1/sales` | Exactly one. Its `total` is computed before the insert and never updated. |
| A CSV row that creates a header | Two: the INSERT, then the update of `total` when the line is added. |
| A CSV line appended to an existing header | One: the update of `total`. |

A consumer therefore sees more than one event for the same `entity_id` and
must be idempotent. It deduplicates by `event_id` before acknowledging an
entry, and re-reads the sale's lines from `api_sales.purchase_line_v1`, never
from the event.

## Consumer behaviour

| Group | Effect |
|---|---|
| `recommendations` | Invalidates its recommendation cache for the customer. |
| `rfm-engine` | Finds the customer of the sale by reading `api_sales.purchase_line_v1` for the entry's `transaction_id` (`entity_id` names the sale, never the customer), then recomputes that customer's profile from their net purchases under the published rule set. It reads the net purchases through the net view of `api_sales` that F21-02 (#770) publishes, so a reversed sale does not count. It touches no other customer (ADR-0063, RN-65). |

Each group deduplicates by `event_id` before acknowledging an entry. On restart
it handles pending entries first, may claim entries idle for more than 60
seconds, and acknowledges only after its effect succeeds. After five failed
deliveries it logs and acknowledges the entry; the outbox row remains in the
seven-day replay log. A sale that raises more than one entry recomputes the same
customer from the same net purchases each time and so gives the same profile, and
an entry for a sale that has since been reversed gives the reversed profile.

## Failure and compatibility

The owner relay waits on `sales_outbox` only as a private wake-up and also
polls at least every five seconds. It relays unrelayed rows in order in batches
of at most 100 and marks them relayed only after Redis accepts the entry. A
crash can duplicate an entry but cannot lose a committed outbox row. The
request path never runs `XADD`.

Changing a field's meaning or encoding, removing a field, changing how
`entity_id` identifies the sale, or adding state to the payload is breaking.
A breaking revision creates `stream:sales.v2.recorded` and a matching `.v2.md`
contract; capture writes both versions until `recommendations` and `rfm-engine` have moved.

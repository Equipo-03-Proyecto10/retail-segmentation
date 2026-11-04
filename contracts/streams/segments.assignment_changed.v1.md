# `stream:segments.v1.assignment_changed`

## Contract

| Field | Value |
|---|---|
| Stream | `stream:segments.v1.assignment_changed` |
| Producer | segments-service |
| Captured table | `public.customer_segment_history` |
| Captured operation | `INSERT` of a row that changes the customer's label or unassigned state, by any writer: `api_segments.apply_assignment_v1` and the web's administrator run |
| Outbox | `segments_events.outbox` |
| Private wake-up channel | `segments_outbox` |
| Consumer groups | `recommendations`, `analytics-kpis` |
| Retention | `XADD MAXLEN ~ 10000` |
| Delivery | At least once |

An AFTER INSERT row trigger on `customer_segment_history` writes one outbox row
in the same transaction as the history row, so a rolled-back change publishes
nothing. A change is a new row whose `label_code` differs from the label of the
row it closes (a `NULL` label, the unassigned result of RN-21, is a state and
differs from any label), or a first row for a customer with none. A row that
re-states the same label, as a full run does for a customer who did not move,
announces nothing. A change caused by a new model version is announced like any
other, and the entry does not say why. A consumer that needs the cause reads it from the
segments service (`cause` on `listVersionedSegmentAssignments`).

The stream is per customer, not per run. `stream:segments.v1.run_recorded`
remains the signal that a run exists and is what flushes run-scoped caches; this
stream says which customers' current assignment moved.

## Entry fields

Every entry is key-only and carries no assignment state:

| Field | Encoding and meaning |
|---|---|
| `event_id` | UUID string generated for the outbox row; the deduplication key. |
| `event_type` | The literal `INSERT`. |
| `entity_id` | The customer's UUID in its canonical lowercase text form. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted as UTC with `Z`. |
| `v` | The literal `1`. |

A customer who changes twice quickly raises two entries. A consumer must be
idempotent, deduplicate by `event_id` before acknowledging an entry, and re-read
the customer's current assignment from `api_segments.current_label_v1`, never
from the event. Because the entry holds no state, an out-of-order delivery
cannot make a consumer apply an older value. The previous label and the version
are not on the entry: a consumer that needs them reads the customer's history
through the segments service's contract.

## Consumers

| Group | Effect |
|---|---|
| `recommendations` | Invalidates its cache of that customer's current label, instead of every customer's, which is all `run_recorded` allows. An incremental change would otherwise flush the whole cache. |
| `analytics-kpis` | Invalidates its cached figures for the segments the customer left and entered, and for the migrations that include them (F26-03). |

`analytics-kpis` joins when its contract and implementation land (F26-01); until
then the group has no reader and `MAXLEN` bounds the stream. Each group
deduplicates by `event_id` before acknowledging an entry. On restart it handles
pending entries first, may claim entries idle for more than 60 seconds, and
acknowledges only after its effect succeeds. After five failed deliveries it logs
and acknowledges the entry; the outbox row remains in the seven-day replay log.

## Failure and compatibility

The owner relay waits on `segments_outbox` only as a private wake-up and also
polls at least every five seconds. It relays unrelayed rows in order in batches
of at most 100 and marks them relayed only after Redis accepts the entry. A
crash can duplicate an entry but cannot lose a committed outbox row.

Changing a field's meaning or encoding, removing a field, changing how
`entity_id` identifies the customer, changing which history rows are announced so
that an existing consumer stops being told, or adding state to the payload is
breaking. A breaking revision creates `stream:segments.v2.assignment_changed` and
a matching `.v2.md` contract; capture writes both versions until both groups have
moved.

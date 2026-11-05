# `stream:rfm.v1.profile_changed`

## Contract

| Field | Value |
|---|---|
| Stream | `stream:rfm.v1.profile_changed` |
| Producer | rfm-engine |
| Captured table | `rfm.customer_profile` |
| Captured operations | `INSERT`, and `UPDATE OF state, rule_set_id, f_count, m_total, r_score, f_score, m_score, label_code`, by any writer |
| Outbox | `rfm_events.outbox` |
| Private wake-up channel | `rfm_outbox` |
| Consumer groups | `model-registry`, `analytics-kpis` |
| Retention | `XADD MAXLEN ~ 10000` |
| Delivery | At least once |

An AFTER INSERT OR UPDATE OF row trigger on `customer_profile` writes one outbox
row in the same transaction as the profile, so a rolled-back recomputation
publishes nothing. A write that changes none of the listed columns publishes
nothing: refreshing `computed_at` or `next_change_at`, or recomputing a customer
whose result did not move, is silent. Recency in days advances with the clock and
is not a captured column, so the passing of a day does not announce every
customer. A scheduled pass, a sale, a reversal and a change of version all
announce the same way: one entry per customer whose profile changed.

## Entry fields

Every entry is key-only and carries no profile state:

| Field | Encoding and meaning |
|---|---|
| `event_id` | UUID string generated for the outbox row; the deduplication key. |
| `event_type` | `INSERT` or `UPDATE`. |
| `entity_id` | The customer's UUID in its canonical lowercase text form. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted as UTC with `Z`. |
| `v` | The literal `1`. |

A customer who changes twice quickly raises two entries. A consumer must be
idempotent, deduplicate by `event_id` before acknowledging an entry, and re-read
the customer's current profile from `api_rfm.current_profile_v1`, never from the
event. Because the entry holds no state, an out-of-order delivery cannot make a
consumer apply an older value.

## Consumers

| Group | Effect |
|---|---|
| `model-registry` | Marks the customer's row in the current distribution sample stale, so data drift is judged against the published version's reference distribution from current profiles (F23-09). |
| `analytics-kpis` | Invalidates its cached figures for the segments and windows the customer belongs to (F26-03). |

Both groups join when their service's contract and implementation land (F22-10 and
F26-01); until then the stream has no reader and `MAXLEN` bounds it. Each group
deduplicates by `event_id` before acknowledging an entry. On restart it handles
pending entries first, may claim entries idle for more than 60 seconds, and
acknowledges only after its effect succeeds. After five failed deliveries it logs
and acknowledges the entry; the outbox row remains in the seven-day replay log.

## What rfm-engine itself consumes

rfm-engine is a consumer of the streams that trigger a recomputation (ADR-0063).
Its consumer group on each is named `rfm-engine`. An entry names a key, never a
fact: the service re-reads the customer's net purchases and recomputes them from
scratch, so a replay or a reordering cannot change a result.

| Stream | Producer | Effect |
|---|---|---|
| `stream:sales.v1.recorded` | sales | Finds the customer of the sale's `transaction_id` and recomputes their profile. Documented in [`sales.recorded.v1.md`](sales.recorded.v1.md). |
| `stream:sales.v1.reversed` | sales | Finds the customer of the reversal's sale and recomputes their profile (ADR-0062). The producer's contract is F21-02 (#770), which lists group `rfm-engine`. |
| The model-registry publication stream | model-registry | Recomputes every customer under the newly published, or rolled-back, version, recorded as a change of version (ADR-0064). The producer's contract is F22-10 (#805). |

## Failure and compatibility

The owner relay waits on `rfm_outbox` only as a private wake-up and also polls at
least every five seconds. It relays unrelayed rows in order in batches of at most
100 and marks them relayed only after Redis accepts the entry. A crash can
duplicate an entry but cannot lose a committed outbox row.

Changing a field's meaning or encoding, removing a field, changing how
`entity_id` identifies the customer, changing which column changes are captured so
that an existing consumer stops being told, or adding state to the payload is
breaking. A breaking revision creates `stream:rfm.v2.profile_changed` and a
matching `.v2.md` contract; capture writes both versions until both groups have
moved.

# `stream:preferences.v1.consent_changed`

## Contract

| Field | Value |
|---|---|
| Stream | `stream:preferences.v1.consent_changed` |
| Producer | customer-preferences-service |
| Captured tables | The consent log and the restriction log, in a customer-preferences service schema (ADR-0046); the schema story, F21-17 (#795), names them |
| Captured operations | `INSERT`, by any writer; both logs are append-only, so there is no `UPDATE` or `DELETE` to capture |
| Outbox | `preferences_events.outbox` |
| Private wake-up channel | `preferences_outbox` |
| Consumer groups | `recommendations`, `campaigns`, `rfm-engine` |
| Retention | `XADD MAXLEN ~ 10000` |
| Delivery | At least once |

An AFTER INSERT row trigger on each of the two logs writes one outbox row in the same
transaction as the decision, so a rolled-back decision announces nothing. When the
event is visible, `api_preferences.consent_state_v1` and
`api_preferences.restriction_v1` already show the change. This stream is separate
from `stream:preferences.v1.changed`, which announces the whole-set replaces of the
two declared sets and is unchanged. One `recordConsentDecisions` call with three
entries raises three events for one customer.

## Entry fields

Every entry is key-only and carries no consent state:

| Field | Encoding and meaning |
|---|---|
| `event_id` | UUID string generated for the outbox row; the deduplication key. |
| `event_type` | The literal `INSERT`. |
| `entity_id` | The customer's UUID in its canonical lowercase text form. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted as UTC with `Z`. |
| `v` | The literal `1`. |

Which log the row came from, the purpose, and whether the decision grants or
withdraws are not in the event: an event for a withdrawal looks exactly like one for a
grant. A consumer re-reads `api_preferences.consent_state_v1` and
`api_preferences.restriction_v1` for that customer and acts on what they say now, so a
replay or a reordering of entries cannot leave it on an older decision. The stream is
an accelerator and not the mechanism: every consumer also reads the views whenever it
decides about a customer, so a withdrawal takes effect at the next read even if an
entry is late.

## Consumers

| Group | Effect |
|---|---|
| `recommendations` | Invalidates its cached result for the customer, so the next read reflects the change and the cache serves nothing older (ADR-0066 section 2) |
| `campaigns` | Re-reads the customer's consent and restrictions before they are next counted in an audience or contacted. What it does with an audience frame already stored is F25-04's decision |
| `rfm-engine` | Re-reads the customer's `PROFILING` decision. On a withdrawal it closes the open assignment and reports the customer as unassigned with reason `PROFILING_WITHDRAWN` (ADR-0063, ADR-0066 section 2) |

Each group joins when the story that implements its reaction lands, and until then
the stream has no reader and `MAXLEN` bounds it. A group deduplicates by `event_id`
before acknowledging an entry, so the several events of one call cost one reaction at
most. On restart it handles pending entries first, may claim entries idle for more
than 60 seconds, and acknowledges only after its effect succeeds. After five failed
deliveries it logs and acknowledges the entry; the outbox row remains in the
seven-day replay log.

## Failure and compatibility

The owner relay waits on `preferences_outbox` only as a private wake-up and also
polls at least every five seconds, so an entry reaches Redis within five seconds of
the commit when Redis is reachable. It relays unrelayed rows in order in batches of at
most 100 and marks them relayed only after Redis accepts the entry. A crash can
duplicate an entry but cannot lose a committed outbox row.

Changing a field's meaning or encoding, removing a field, changing how `entity_id`
identifies the customer, or adding state to the payload is breaking. A breaking
revision creates `stream:preferences.v2.consent_changed` and a matching `.v2.md`
contract; capture writes both versions until every group has moved.

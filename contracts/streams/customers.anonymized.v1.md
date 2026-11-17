# `stream:customers.v1.anonymized`

## Contract

| Field | Value |
|---|---|
| Stream | `stream:customers.v1.anonymized` |
| Producer | customers |
| Captured table | `public.customer` |
| Captured operation | `UPDATE` that sets `anonymized_at` from null to a time; a customer is anonymized once, so it is announced once |
| Outbox | `customers_events.outbox` |
| Private wake-up channel | `customers_outbox` |
| Consumer groups | `customer-preferences`, `users` |
| Retention | `XADD MAXLEN ~ 10000` |
| Delivery | At least once |

An AFTER UPDATE row trigger on `customer` writes one outbox row in the same
transaction that removes the identity, so a rolled-back anonymization announces
nothing, and an anonymization that is repeated announces nothing, because it
changes no row (ADR-0066 section 3). When the event is visible, the customer is
already flagged `anonymized` in `api_customers.customer_ref_v1`.

## Entry fields

Every entry is key-only and carries no identity, not even the placeholder:

| Field | Encoding and meaning |
|---|---|
| `event_id` | UUID string generated for the outbox row; the deduplication key. |
| `event_type` | The literal `UPDATE`. |
| `entity_id` | The anonymized customer's `customer_id`, as lower-case hyphenated text. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted as UTC with `Z`. |
| `v` | The literal `1`. |

Whether the cause was a request or the retention period is not in the event; it
is the `reason_code` of the `CUSTOMER_ANONYMIZED` domain event (ADR-0072).

## Consumers

| Group | Effect |
|---|---|
| `customer-preferences` | Empties the customer's declared preferences and inferred interests and accepts no further decision for the customer (409 `customer_anonymized`); the consent log is kept (ADR-0066 section 3). |
| `users` | Deactivates the login account the customer was linked to, if any. |

A consumer first reads `api_customers.customer_ref_v1.anonymized` and acts only
when it is true. The groups join when each service implements its reaction; until
then the stream has no reader and `MAXLEN` bounds it. A group deduplicates by
`event_id` before acknowledging an entry, handles pending entries first on
restart, may claim entries idle for more than 60 seconds and acknowledges only
after its effect succeeds. After five failed deliveries it logs and acknowledges
the entry; the outbox row remains in the seven-day replay log.

## Propagation bound

The time from the commit of the outbox row to the group's acknowledgement of
its entry, under the controlled load of `ops/propagation/` (F20-06, #764).
`ops/propagation/propagation.py check` fails a run whose p95 or maximum
exceeds the figure below, or that leaves an entry unacknowledged.

| Group | p95 (ms) | Max (ms) |
|---|---:|---:|
| `customer-preferences` | 3000 | 5000 |
| `users` | 3000 | 5000 |

Not measured yet: `customer-preferences`, `users`.
Their bound is the default, and it holds from the first run that measures them.

## Failure and compatibility

The owner relay waits on `customers_outbox` only as a private wake-up and also
polls at least every five seconds. It relays unrelayed rows in order in batches
of at most 100 and marks them relayed only after Redis accepts the entry. A crash
can duplicate an entry but cannot lose a committed outbox row.

Changing a field's meaning or encoding, removing a field, or adding state to the
payload is breaking. A breaking revision creates `stream:customers.v2.anonymized`
and a matching `.v2.md` contract; capture writes both versions until every group
has moved.

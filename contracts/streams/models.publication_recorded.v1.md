# `stream:models.v1.publication_recorded`

## Contract

| Field | Value |
|---|---|
| Stream | `stream:models.v1.publication_recorded` |
| Producer | model-registry |
| Captured table | `models.model_publication` |
| Captured operation | `INSERT`, by any writer; a publication and a rollback are both inserts |
| Outbox | `models_events.outbox` |
| Private wake-up channel | `models_outbox` |
| Consumer groups | `rfm-engine` |
| Retention | `XADD MAXLEN ~ 10000` |
| Delivery | At least once |

An AFTER INSERT row trigger on `model_publication` writes one outbox row in the
same transaction as the publication, so a rolled-back publication announces
nothing. When the event is visible, the version it announces is already the one
`api_models.published_version_v1` returns.

## Entry fields

Every entry is key-only and carries no version state:

| Field | Encoding and meaning |
|---|---|
| `event_id` | UUID string generated for the outbox row; the deduplication key. |
| `event_type` | The literal `INSERT`. |
| `entity_id` | Base-10 text form of the publication's `publication_id`. |
| `occurred_at` | RFC 3339 timestamp with an offset, emitted as UTC with `Z`. |
| `v` | The literal `1`. |

Whether the entry is a publication or a rollback is not in the event. A consumer
re-reads `api_models.published_version_v1` and recomputes under whatever version
is published now, so a replay or a reordering of entries cannot leave it on an
older version.

## Consumers

| Group | Effect |
|---|---|
| `rfm-engine` | Recomputes every customer under the newly published, or restored, version, recorded as a change of version (ADR-0063, ADR-0064). |

The group joins when rfm-engine implements its reaction to a change of version; until then the
stream has no reader and `MAXLEN` bounds it. The group deduplicates by `event_id`
before acknowledging an entry. On restart it handles pending entries first, may
claim entries idle for more than 60 seconds, and acknowledges only after its
effect succeeds. After five failed deliveries it logs and acknowledges the entry;
the outbox row remains in the seven-day replay log.

## Failure and compatibility

The owner relay waits on `models_outbox` only as a private wake-up and also polls
at least every five seconds. It relays unrelayed rows in order in batches of at
most 100 and marks them relayed only after Redis accepts the entry. A crash can
duplicate an entry but cannot lose a committed outbox row.

Changing a field's meaning or encoding, removing a field, changing how
`entity_id` identifies the publication, or adding state to the payload is
breaking. A breaking revision creates `stream:models.v2.publication_recorded` and
a matching `.v2.md` contract; capture writes both versions until every group has
moved.

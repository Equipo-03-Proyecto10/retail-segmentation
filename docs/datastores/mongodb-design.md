# MongoDB — design

**Status: design only. Not implemented, not installed, not connected to.**
See [ADR-0005](../adr/0005-document-mongodb-and-redis-designs-without-implementing-them.md).

Where PostgreSQL holds the structured, transactional side of the business, this
design covers what changes shape over time and grows quickly: derived analytics
whose schema is expected to evolve between model versions.

Every collection here belongs to a module deferred in
[`roadmap.md`](../roadmap.md) — RFM computation, batch clustering, segment
history, segment migration. None of them exists yet.

## Collections

| Collection | Grain | Purpose |
|---|---|---|
| `consumption_profile` | One document per customer | Current profile snapshot plus a short embedded history |
| `rfm_snapshot` | One document per customer per period | Recency, frequency and monetary scores as computed that period |
| `segment_migration` | One document per detected segment change | Append-only migration trail |
| `clustering_model` | One document per model run or version | Parameters, centroids, version |
| `recommendation` | One document per generated batch | Ranked items with scores and context |
| `experiment_event` | One document per exposure or conversion | A/B test event stream |

## Document shapes

```javascript
// consumption_profile
{
  "_id": "customer_uuid_123",
  "customer_ref": "customer_uuid_123",   // logical reference into PostgreSQL
  "favourite_categories": ["dairy", "snacks"],
  "dominant_channel": "mobile_app",
  "average_monthly_spend": 850.30,
  "frequency_trend": "rising",
  "updated_at": ISODate("2026-08-30T00:00:00Z"),
  "history_summary": [                   // embedded: few, always read together
     { "period": "2026-07", "spend": 780.10, "segment": "SEG_LOYAL" },
     { "period": "2026-08", "spend": 850.30, "segment": "SEG_LOYAL" }
  ],
  "schema_version": 2
}

// segment_migration — large, append-only
{
  "_id": ObjectId("..."),
  "customer_ref": "customer_uuid_123",
  "previous_segment": "SEG_OCCASIONAL",
  "new_segment": "SEG_LOYAL",
  "reason": "frequency_increase",
  "rfm_at_the_time": { "r": 5, "f": 4, "m": 4 },
  "detected_at": ISODate("2026-08-15T03:00:00Z"),
  "model_ref": "clustering_model_v7"
}

// recommendation
{
  "_id": ObjectId("..."),
  "customer_ref": "customer_uuid_123",
  "generated_at": ISODate("2026-09-01T00:00:00Z"),
  "method": "collaborative",
  "items": [
     { "product_ref": 1042, "score": 0.87 },
     { "product_ref": 2093, "score": 0.81 }
  ],
  "experiment_context": "EXP_2026_09_A"
}
```

## Embedding versus referencing

Embed what is small, bounded, and always read with its parent:
`history_summary` inside `consumption_profile` is a handful of periods that is
never queried on its own.

Reference what grows without bound or is queried independently:
`customer_ref`, `product_ref` and `model_ref` point at PostgreSQL rows or at
other collections, because those entities have their own lifecycle and their own
size.

## Indexes

| Collection | Index | Query it serves |
|---|---|---|
| `consumption_profile` | `{customer_ref: 1}` unique | Fetch one customer's profile |
| `segment_migration` | `{customer_ref: 1, detected_at: -1}` | One customer's migration history, newest first |
| `segment_migration` | `{new_segment: 1}` | Flows into a segment |
| `recommendation` | `{customer_ref: 1, generated_at: -1}` | Latest recommendations for a customer |
| `experiment_event` | `{experiment_ref: 1, group: 1, event_type: 1}` | Conversion rate per arm |
| `recommendation` | TTL, optional | Only if recommendations are not kept indefinitely |

## Versioning

`schema_version` on the flexible documents lets the shape evolve without a
destructive migration: the service reading a document decides how to interpret
it. `clustering_model` is versioned explicitly — `model_v7`, `model_v8` — and a
version is never overwritten, because comparing runs is the point of keeping
them.

## Growth

`segment_migration` and `experiment_event` grow fastest. Shard on
`customer_ref` when volume justifies it, and archive events older than a chosen
window to cold storage. The compound indexes above are chosen so that the two
most frequent queries — one customer's history, one experiment's conversion —
stay bounded as the collections grow.

# Redis Stream contracts

Every stream published across a boundary has a producer-owned contract named
`contracts/streams/<producer>.<event>.v1.md`. `<producer>` is the suffix of the
producer's `svc_` PostgreSQL role. That file contracts the stream
`stream:<producer>.v1.<event>` and records:

- the captured table and operations;
- the meaning and encoding of `entity_id`;
- the key-only fields `event_id`, `event_type`, `entity_id`, `occurred_at`, and
  `v` (events never carry entity state);
- every consumer group, named after the consuming service; and
- replay, failure, and compatibility behavior.

Producers use `XADD` with `MAXLEN ~ 10000`. Delivery is at least once, and each
consumer deduplicates by `event_id` before acknowledging an entry. State is
read through the producer's versioned database contract.

A breaking change creates `stream:<producer>.v2.<event>` and a `.v2.md`
contract beside v1. The capture path writes both versions until all consumer
groups have moved.

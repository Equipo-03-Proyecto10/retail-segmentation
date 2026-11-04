# ADR-0074 — engagement relays interaction facts on a Redis stream, from an outbox kept on the event document

**Status:** Proposed
**Owner:** Max
**Issue:** #948
**Supersedes:** —
**Superseded by:** —

---

## Context

engagement keeps every event in MongoDB (`engagement_event`, `engagement_daily`, [ADR-0038](0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md)).
It owns no PostgreSQL table, publishes no `api_engagement` object and relays no stream. Two contracts under review need its
facts: analytics-kpis (#930) lists `api_engagement.interaction_v1` as "required, not contracted" for the conversion and
recommendation KPIs, and experimentation (#798, #935) consumes `CAMPAIGN_EXPOSURE`. [ADR-0059](0059-four-new-services-join-the-fifteen-and-six-capabilities-fold-into-the-rule-they-extend.md)
says a KPI is computed only from `api_*` objects, and [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)
allows a boundary to be read only through a producer's versioned `api_*` view or function, or through a Redis stream relayed
from the producer's own transactional outbox. No mechanism exists yet that engagement can use.

The obstacle is specific. ADR-0044's outbox is a PostgreSQL table written in the same transaction as the fact. engagement's
store is a standalone `mongod` (ADR-0038), where an event and a second document cannot be written atomically. Judgement
calls: whether to give engagement a second datastore, and how to get at-least-once relay without a transaction across two
documents.

## Decision

engagement relays its interaction facts on one versioned Redis stream, `stream:engagement.v1.interaction_recorded`, from an
outbox that lives on the event document itself. This is option 1 of the issue. The outbox cannot be a second collection
(no multi-document transaction), but a single-document write is atomic, so `engagement_event` gains `relay_pending`, an
array of the notices still to publish (`RECORDED`, then `VOIDED`). An insert writes `["RECORDED"]`; a void, in the same
`findOneAndUpdate` that sets `voided` and the history entry, appends `"VOIDED"`. A partial index on `relay_pending` (non-empty)
keeps the scan cheap. engagement's relay, a thread of the same container, polls every second, runs `XADD
stream:engagement.v1.interaction_recorded MAXLEN ~ 10000 *` on `redis-state` for each pending notice in `occurred_at` order,
then `WAITAOF 1 0 2000` and `$pull`s the notice; delivery is at least once and consumers dedupe by the stream's `event_id`
(`<event_id>:recorded` or `<event_id>:voided`). Types carried are impressions, `QR_SCAN`, `RECO_CLICK` and `CAMPAIGN_EXPOSURE`
as the taxonomy of #935 defines them.

Unlike ADR-0044's streams, the payload carries the fact, not only ids: `event_id`, `v`, `notice`, `type`, `store_id`,
`product_id`, `customer_id`, `campaign_id`, `label_code`, `rank`, `occurred_at`. There is no engagement view to re-read state
through, and the facts are small and carry no names, emails or coordinates (ADR-0038). Each consumer keeps its own
projection in its own schema. analytics-kpis never consumes the stream: it reads the consumer that owns the projection,
through that owner's `api_*` view (experimentation for exposures, recommendations for `RECO_CLICK`), and the view's name
and shape belong to that owner's contract. A void reaches every projection as a `VOIDED` notice, and projections count only
what is not voided, as engagement's own summaries do.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Option 2: also write facts to PostgreSQL in engagement's schema and publish `api_engagement.*_v1` views | A second datastore for one service, a dual write that is not atomic across MongoDB and PostgreSQL (the reconciliation problem ADR-0038 avoided by refusing a second copy of purchases), and a change to ADR-0038's "holds no PostgreSQL table". It would also put high-volume, write-heavy events into the database the web system shares |
| Option 3: consumers use only summaries engagement already owns (`engagement_daily`) | Daily counts by store and type cannot give an exposure per customer and campaign, so experimentation could not tell who saw what, and KPIs lose the customer and campaign dimension. It narrows the product, not just the mechanism |
| A separate `engagement_outbox` collection | Writing the event and its outbox row are two operations on a standalone `mongod`; a crash between them loses a notice or publishes one for an event that never committed. Converting to a replica set for transactions changes the deployment of ADR-0038 for no other gain |
| Change streams on `engagement_event` | Requires a replica set, and ties the relay's resume token to oplog retention instead of to a replayable record |
| analytics-kpis reads engagement's MongoDB or HTTP API | Breaks ADR-0034, ADR-0035 and ADR-0044 |

## Consequences

**What this makes easy.** No new datastore and no schema change in `sql/01_schema.sql`. The pattern mirrors ADR-0044's
outbox-and-stream, so consumers follow the same group, claim and dedupe rules. A relay outage loses nothing: pending
notices stay on the documents and replay when it returns. A void is one more notice, so it propagates by the same route.

**What this makes hard.**
- *Memory.* The relay adds no process. Stream entries are about 300 bytes, so `MAXLEN ~ 10000` holds about 3 MB on `redis-state`. Each consumer adds its own projection storage.
- *Latency.* The poll adds up to 1 s, plus consumer handling; a fact is visible in a projection in seconds, not at write time. A KPI built on it is eventually consistent, and the contract must say so.
- *Voiding.* A void arrives after the fact, so a projection read between the two counts the event. Past the stream's retention window a lagging consumer cannot recover from the stream alone; engagement must offer a rebuild path from `engagement_event` (a maintenance command, not an endpoint, like the daily rollup).
- Ordering is per notice, not global: a `VOIDED` may be processed before its `RECORDED` after a redelivery, so consumers apply notices idempotently by `event_id` and keep the void.
- The outbox flag adds one array and one partial index to a write-heavy collection, and widens ADR-0038's validator by `relay_pending`.

**What must now be true elsewhere.**
- `contracts/streams/engagement.interaction_recorded.v1.md` is published with its consumers and payload before any implementation; engagement's Redis ACL grants it `XADD` on that stream and each consumer's group its read permissions.
- `contracts/engagement/`, `docs/services/engagement.md` ("Events") and `docs/services/analytics-kpis.md` name this transport; #930's `api_engagement.interaction_v1` is replaced by the owners' views; #798's exposure criterion names the stream and experimentation's projection.
- ADR-0044 is not edited. On acceptance this record extends its outbox clause to a MongoDB producer, and its index status becomes "Partially superseded by 0074" in the same pull request that accepts this one. ADR-0038's collection fields change by the same step.

## Compliance

- A contract test: `contracts/streams/check_streams.sh` fails if the stream has no producer or consumer entry, or if the producer is not engagement.
- A service test with the stream stubbed offline: a recorded event and a void leave `relay_pending` non-empty, and after the relay runs it is empty and the stream holds one entry per notice.
- A grep in CI over `services/` that no service other than engagement reads `engagement_event` or issues `XADD` on `stream:engagement.*`.

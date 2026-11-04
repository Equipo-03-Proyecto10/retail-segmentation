# ADR-0075 — engagement relays interaction facts on a Redis stream, from an outbox kept on the event document

**Status:** Proposed
**Owner:** Max
**Issue:** #948
**Supersedes:** in part ADR-0044 (the outbox clause, extended to a MongoDB producer, and the key-only payload rule, for this stream only); in part ADR-0038 (engagement_event's validated fields gain relay_pending)
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
`findOneAndUpdate` that sets `voided` and the history entry, appends `"VOIDED"`. A partial index on `relay_pending` with the filter
`{"relay_pending.0": {$exists: true}}` (non-empty; `$pull` leaves `[]`, so `$exists` on the field itself would not do) keeps the scan
cheap, and the relay's query repeats that predicate. engagement's relay, a thread of the same container, polls every second and, for each pending notice in `occurred_at` order,
publishes one entry to `redis-state` with `XADD stream:engagement.v1.interaction_recorded MAXLEN ~ 10000 *` and
`WAITAOF 1 0 2000`, then `$pull`s the notice. It does so through the kit, not by calling `XADD` itself (see below); delivery is at least once and consumers dedupe by the stream's `event_id`
(`<event_id>:recorded` or `<event_id>:voided`). The kinds carried are `RECO_IMPRESSION`, `RECO_CLICK` and `CAMPAIGN_EXPOSURE`, as the taxonomy of #935 defines them.
`QR_SCAN` is not carried, because no consumer acts on it; the other four kinds (`STORE_VISIT`, `CART_ADD`, `CART_REMOVE`
and `NAVIGATION`) are not carried either, and a consumer that needs one writes its own record.

**The publisher is the kit's.** ADR-0044's Compliance (check 8) and `.github/scripts/guards.sh` forbid `XADD` in any service
except through the kit, and forbid MongoDB or PostgreSQL code in `packages/` outside engagement or the outbox helper. The kit
therefore gains a source-agnostic stream publisher under the relay helpers of ADR-0045: it takes a stream name and a field map,
runs `XADD ... MAXLEN ~ 10000` and `WAITAOF`, and encodes the entry. It names neither MongoDB nor PostgreSQL. The polling of
`relay_pending` stays in engagement and calls it. Both guards stay unchanged.

**This stream carries the fact, and ADR-0044's key-only rule is superseded for it alone.** ADR-0044 says "no event carries
state, only keys the consumer re-reads through the producer's views", and `contracts/streams/README.md` lists the key-only
fields. That rule exists so that a consumer re-reads state through a view. engagement publishes no view and holds nothing in
PostgreSQL, so there is nothing to re-read, and the fact is the only thing a consumer can get. Every other stream stays key-only.
The payload carries the fact, not only ids: `event_id`, `v`, `notice`, `type`, `store_id`,
`product_id`, `customer_id`, `campaign_id`, `label_code`, `rank`, `occurred_at`. There is no engagement view to re-read state
through, and the facts are small and carry no names, emails or coordinates (ADR-0038). Each consumer keeps its own
projection in its own schema: **recommendations** owns the projection of `RECO_IMPRESSION` and `RECO_CLICK`, the interactions
with its own recommendations, and **experimentation** owns `CAMPAIGN_EXPOSURE`. analytics-kpis never consumes the stream
(ADR-0059: a KPI is computed from `api_*` objects): it reads the owner of the projection through that owner's `api_*` view, and the
view's name and shape belong to that owner's contract.

ADR-0038 rejected a second copy of purchases and ADR-0044 rejected full projections, and a consumer-kept projection is
acceptable here for three reasons. engagement publishes no PostgreSQL view, so the stream is the only route. Each projection
holds only the kinds its owner acts on and nothing beyond the fact. And a projection is derived: it is rebuilt from the stream
(below), so it never becomes a second source of truth that could disagree with engagement. A void reaches every projection as a `VOIDED` notice, and projections count only
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
- *Voiding.* A void arrives after the fact, so a projection read between the two counts the event. Past the stream's retention window a lagging consumer cannot recover from the stream alone. The rebuild path is a maintenance command of engagement, not an endpoint (like the daily rollup), that re-queues `relay_pending` for the events of a day range from `engagement_event`. The relay then redelivers them to every consumer group, consumers dedupe by `event_id`, and a consumer deployed later joins the same way, because its group starts at `$` and sees no history. Unlike ADR-0044's outbox there is no seven-day replay log, since a relayed notice is `$pull`ed.
- Ordering is per notice, not global: a `VOIDED` may be processed before its `RECORDED` after a redelivery, so consumers apply notices idempotently by `event_id` and keep the void.
- The outbox flag adds one array and one partial index to a write-heavy collection, and widens ADR-0038's validator by `relay_pending`.

**What must now be true elsewhere.**
- `contracts/streams/engagement.interaction_recorded.v1.md` is published with its consumers and payload before any implementation; engagement's Redis ACL grants it `XADD` on that stream and each consumer's group its read permissions.
- `contracts/engagement/`, `docs/services/engagement.md` ("Events") and `docs/services/analytics-kpis.md` name this transport; #930's `api_engagement.interaction_v1` is replaced by the owners' views; #798's exposure criterion names the stream and experimentation's projection.
- On acceptance, the Status lines of ADR-0044 and ADR-0038 become `Partially superseded by ADR-0075`, the only edit an accepted record receives; the new field `relay_pending` is defined here, in the same pull request that accepts this one.
- The story that publishes the stream contract also amends `contracts/streams/README.md`: its list of key-only fields gets a carve-out naming this stream, which carries the fact. It extends `contracts/streams/check_streams.sh` to check engagement's stream, whose name is not in `sql/01_schema.sql`, and adds the kit's stream publisher (ADR-0045) and the `+waitaof` and `+xadd` entries of engagement's Redis ACL line.
- **Expand before deploy (ADR-0038, ADR-0040).** An operator applies the committed `services/engagement/mongo/init.js`, with the widened validator and the partial index, before the engagement version that writes `relay_pending` is deployed; otherwise the validator's `additionalProperties: false` refuses every insert. Consumers deploy on their own: no service has a `depends_on` on another.

## Compliance

- A contract test: today `contracts/streams/check_streams.sh` reads only the stream names in `sql/01_schema.sql`, which do not include this one. The stream story extends the script to fail when this stream has no contract file, no consumer entry, or a producer other than engagement.
- A service test with the stream stubbed offline: a recorded event and a void leave `relay_pending` non-empty, and after the relay runs it is empty and the stream holds one entry per notice.
- The existing guards stay unchanged and keep passing: `.github/scripts/guards.sh` forbids `XADD` in any service outside the kit and MongoDB outside engagement. A further grep over `services/` checks that no service other than engagement reads `engagement_event`.

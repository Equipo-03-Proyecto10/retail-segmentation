# ADR-0038 — MongoDB holds in-store engagement, owned by engagement-service alone

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The second delivery requires MongoDB in real operation: insert, query, update, aggregation and indexes, on data the system
actually uses. [`../datastores/mongodb-design.md`](../datastores/mongodb-design.md) still proposes six collections, and each
now duplicates a fact PostgreSQL holds and the web reads: segment history and runs (ADR-0017), experiment events (ADR-0019) and
the RN-34 consumption profile. An earlier draft replaced them with one `customer_activity` collection of QR scans, GPS
check-ins, recommendation clicks and purchases. Two of those four do not belong in MongoDB:
- **Check-ins carry a rule that needs a join** with `stores.store_location`, and an override is an append-only review. They
  live in the `checkins` schema of PostgreSQL ([ADR-0046](0046-service-owned-data-lives-in-service-schemas.md)), next to the store locations they are checked against.
- **Purchases are already `transaction` rows** ([ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md)).
  A `PURCHASE` document would be a second copy, kept in step by a consumer, and the two would eventually disagree.

What is left is new data no table holds: a product QR scanned on the floor and a tap on a shown recommendation, both from
Android ([ADR-0041](0041-android-and-electron-clients-run-different-processes-over-different-formats.md)). They are written far
more often than read, read mostly as counts, never joined with a sale, and shaped by type.
[ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) gives every fact one service writer
and [ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md) every service one rule; a shared MongoDB
database would break both. Three things are judgement calls, each costed below:
- a standalone `mongod`, so an event and its rollup cannot be written in one transaction;
- the built-in `readWrite` role, rather than a custom role without `remove`;
- a "day" is a UTC calendar day.

## Decision

MongoDB holds exactly one application database, `engagement`. Only engagement-service reads or writes it, as the MongoDB user
`svc_engagement`, defined in that database and holding `readWrite` on it and on nothing else. The database has two collections.
`engagement_event` records each `QR_SCAN` or `RECO_CLICK` once per client event (RN-55), under a `$jsonSchema` validator and a
unique `client_event_id`. An event is voided with a reason, never deleted. `engagement_daily` is its rollup by store, day and
type; live counters in `redis-cache` cache that rollup and are rebuilt from it. Before it writes, the service validates every
identifier against three PostgreSQL contract views: `api_products.product_ref_v1`, `api_stores.store_directory_v1` and
`api_customers.customer_ref_v1`. It holds no PostgreSQL table. Check-ins and purchases stay in PostgreSQL.

### The two collections

`engagement_event` documents hold: `_id`, the server-assigned `event_id` (a UUID string, the API's `{event_id}`);
`client_event_id`, a UUID generated on the device once per scan or tap, unique; `type`, `QR_SCAN` or `RECO_CLICK`; `store_id`
and `product_id`, ints (the QR payload is the `product_id`); an optional `customer_id`, a UUID string; for `RECO_CLICK` only,
`label_code` (at most 40 characters, as `segment_label.label_code`) and `rank` (an int ≥ 1, the position shown); `recorded_by`,
the token's `sub`; `occurred_at`, the device's time; `recorded_at`, the server's time; `voided`, `false` on insert; and
`history`, one `{action: "VOIDED", reason, by, at}` per change. The validator is a strict `$jsonSchema`, with validation action
`error`, `additionalProperties: false` and a `oneOf` over the two shapes, so a scan never carries a label or rank, and names,
emails and coordinates stay out. The indexes are `{client_event_id: 1}` unique (a retry hits `E11000` instead of inserting
twice), `{store_id: 1, occurred_at: -1}`, `{type: 1, occurred_at: -1}` and `{customer_id: 1, occurred_at: -1}`.

`engagement_daily` holds one document per store, UTC day (`YYYY-MM-DD` of `occurred_at`) and type, under a unique index on
`{store_id, day, type}`, with a `count` of the events not voided. An insert runs `findOneAndUpdate` with `{$inc: {count: 1}}`,
`upsert: true` and `returnDocument: "after"`; a void runs it with `{$inc: {count: -1}}`. The rollup is rebuildable: a
maintenance command (not an endpoint) runs `$match: {voided: false}` and `$group` by store, type and UTC day over
`engagement_event`, and `$merge`s the result into `engagement_daily` for a day range.

### Operations and live counters

| Operation | Endpoint | MongoDB |
|---|---|---|
| Insert | `POST /v1/engagement-events` | `insertOne`, then the rollup `$inc`. On `E11000`, a stored event with the same `type`, `store_id` and `product_id` answers 200 with the original and writes nothing; otherwise 409 `client_event_conflict` |
| Query | `GET /v1/engagement-events` (`type`, `store_id`, `customer_id`, `voided`, `from`, `to`, `page`, `size`) and `GET /v1/engagement-events/{event_id}` | `find` with a range on `occurred_at`, sorted `occurred_at: -1`, each filter served by one index (`IXSCAN` in `explain`) |
| Update | `POST /v1/engagement-events/{event_id}/void`, `ADMIN` only, with a `reason` | `updateOne({_id, voided: false}, {$set: {voided: true}, $push: {history: …}})`, then `$inc: -1`. Already voided: 409 `already_voided` |
| Aggregation | `GET /v1/engagement-summaries` (`from`, `to`, `store_id`, `group_by=store\|day\|type`) | `$match` on `day` and `store_id`, `$group` with `$sum: "$count"`, `$sort`, over `engagement_daily` |

Live counters, `engagement:live:{store_id}:{yyyymmdd}:{type}`, live in `redis-cache` with a 48 h TTL
([ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)). After the rollup update the
service runs `INCR` (or `DECR`); a result of 1 (or −1) means the key was missing, so it overwrites it with the count
`findOneAndUpdate` returned. A one-store summary of the current UTC day reads the counter, and falls back to `engagement_daily`
on a miss or when `redis-cache` is unreachable. Every other summary runs the pipeline.

### Write-time validation and who creates the shape

`svc_engagement` holds SELECT on exactly the three views and nothing else in PostgreSQL
([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)). The service refuses 422
`unknown_store` (the store must exist and be active), `unknown_product` (it must exist), `unknown_customer` (when one is given)
and `occurred_in_future` (beyond five minutes ahead). `label_code` is checked against its pattern only. The validator, indexes
and user are created by `services/engagement/mongo/init.js`, idempotent and run in mongosh by an administrator before the
service version that needs it (expand before deploy). The service never runs `collMod` or `createUser`, and its `/health/ready`
answers 503 `schema_not_ready` while a collection or index is missing.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| The six collections of [`mongodb-design.md`](../datastores/mongodb-design.md) (`consumption_profile`, `rfm_snapshot`, `segment_migration`, `clustering_model`, `recommendation`, `experiment_event`) | Each copies a fact PostgreSQL already holds and the web already shows (ADR-0017, ADR-0019, RN-34). Two copies of segment history disagree eventually, and nothing decides which is right. A service that wrote them would also be a second writer of facts the web's administrator run owns |
| Check-ins in MongoDB, with GeoJSON points under a `2dsphere` index (the earlier draft) | The geofence check needs the store's location in the same place as the claim. A `2dsphere` index on the claim does not provide that: the location lives in `stores.store_location`. The override is an append-only, attributed review, which a CHECK and a trigger enforce in PostgreSQL. Putting check-ins here would also give MongoDB two owners, or engagement two rules |
| Purchases in MongoDB, fed from the sales stream | A second copy of `transaction`, kept in step by an at-least-once consumer. It adds nothing a query on `transaction_line` cannot answer, and it is a fact that changes for a reason (a sale) that is not this service's rule |
| Several services own collections in one MongoDB database | It recreates the shared store that ADR-0034's per-boundary grants avoid in PostgreSQL. A collection's shape could change under a service that does not own it, and "one owner per fact" could not be checked from `db.getUsers()` |
| A custom role with `find`, `insert` and `update` but no `remove` | It would make "never deleted" a database guarantee. But the rollup rebuild (`$merge`) and the start-up index checks need more actions than that, and a missing action fails at run time as an authorization error. The boundary that matters for independence is one user on one database. "Never deleted" is enforced in the service and checked in CI. This is the weakest point of the decision and is revisited if MongoDB holds anything sensitive |
| Keep engagement in PostgreSQL too, and skip MongoDB | It fails the course requirement that MongoDB be in real operation |

## Consequences

**What this makes easy.**
- Every operation the course names is a real endpoint in the demonstration: an insert on every scan and tap from Android,
  queries and voids from Electron, an aggregation on every summary.
- No fact lives in two stores, and a retried tap or scan is one event: the unique index decides that, not the client.
- Losing `redis-cache` or `engagement_daily` loses nothing; the rebuild recomputes both from the events.

**What this makes hard.**
- **No transaction across the event and its rollup.** A crash between `insertOne` and the `$inc` leaves the rollup one short
  until the rebuild runs. A single-node replica set would fix that, at the cost of an oplog; the team prefers a rebuildable rollup.
- **No referential integrity.** Ids are checked once, at write time, and no stream tidies them, so a store or product the web
  later deletes keeps its events. Electron renders an id that no longer resolves as unknown instead of failing.
- **`readWrite` can delete and drop.** "Never deleted" rests on the service's code, a CI grep and review, not on the database.
- **A day is a UTC day.** The seeded stores run from Mérida to Tijuana, so an evening scan counts on the next UTC date.
  Store-local days would need each store's time zone, which no table holds.
- **The web cannot see any of this.** Engagement appears in Electron only, so the demonstration's step 5 uses a sale, not a scan.
- **The 4NF argument does not apply here.** The data model must say why the document shape is acceptable: one document per
  event, no repeating group except `history`, which is bounded and always read with its event.
- **MongoDB costs memory:** the largest infrastructure container in the budget of ADR-0040, for two collections.

**What must now be true elsewhere.**
- [`../datastores/mongodb-design.md`](../datastores/mongodb-design.md) is rewritten as this specification; its six collections
  are withdrawn, with this record as the reason. `docs/data-model.md` documents the two collections, the boundary that keeps
  them out of the relational model, and the shape justification.
- `contracts/engagement/openapi.yaml` and `engagement.xsd` expose exactly the operations above, with these codes (ADR-0035).
- `deploy/infra/compose.yaml` runs MongoDB 7 at a pinned image, with authorization on and no published port
  ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)); only engagement's `compose.yaml` sets
  `MONGO_URI`, which `.env.example` documents with a local default.
- `sql/01_schema.sql` grants `svc_engagement` SELECT on the three views and nothing else; `contracts/db/grants.csv` lists
  exactly those three rows. Its Redis ACL allows `~engagement:live:*` and the liveness selector (ADR-0037).
- ADR-0046 holds the check-in tables, and ADR-0041's Android client sends one `RECO_CLICK` per tap, with a fresh `client_event_id`.
- `services/engagement/mongo/seed.js` loads at least 30 events per type over seeded stores, products and customers, with fixed
  `client_event_id`s so a second run inserts nothing, and ends with the rollup rebuild.

## Compliance

In mongosh as `svc_engagement` (`docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec mongodb mongosh "$MONGO_URI"`):

```javascript
db.runCommand({connectionStatus: 1, showPrivileges: true}).authInfo.authenticatedUserRoles  // [ { role: "readWrite", db: "engagement" } ]
db.getCollectionNames().sort()                                                              // [ "engagement_daily", "engagement_event" ]
db.getCollectionInfos({name: "engagement_event"})[0].options.validator.$jsonSchema !== undefined   // true
db.engagement_event.getIndexes().map(i => [i.key, !!i.unique])   // exactly the four indexes above, client_event_id unique
db.engagement_event.insertOne({_id: "x", client_event_id: "y", type: "QR_SCAN", store_id: 1, product_id: 1, rank: 1,
  recorded_by: "z", occurred_at: new Date(), recorded_at: new Date(), voided: false, history: []})  // "Document failed validation"
const p = db.engagement_event.find({store_id: 1, occurred_at: {$gte: ISODate("2026-10-01")}}).sort({occurred_at: -1}).explain("executionStats")
JSON.stringify(p.queryPlanner.winningPlan).includes('"indexName":"store_id_1_occurred_at_-1"')   // true
const rebuilt = db.engagement_event.aggregate([{$match: {voided: false}}, {$group: {_id: {store_id: "$store_id", type: "$type",
  day: {$dateToString: {format: "%Y-%m-%d", date: "$occurred_at", timezone: "UTC"}}}, count: {$sum: 1}}}]).toArray()
[rebuilt.filter(r => (db.engagement_daily.findOne({store_id: r._id.store_id, day: r._id.day, type: r._id.type}) || {count: -1}).count !== r.count).length,
 db.engagement_daily.countDocuments({count: {$gt: 0}}) - rebuilt.length]                   // [ 0, 0 ]
```

CI runs these from the repository root, and each must print nothing:

```bash
grep -rlnE 'MONGO_URI|pymongo|motor|mongodb' services/ packages/ bench/ | grep -vE '^services/engagement/'
grep -rnE '^\s*mongo(db)?:' services/*/compose.yaml
grep -rnE '\b(delete_one|delete_many|find_one_and_delete|drop_collection|drop_database|collMod|createUser)\b|\.drop\(' services/engagement/src/
psql -d retail -Atc "SELECT table_schema||'.'||table_name||':'||privilege_type FROM information_schema.role_table_grants WHERE grantee = 'svc_engagement'" \
  | grep -vxE 'api_(products\.product_ref_v1|stores\.store_directory_v1|customers\.customer_ref_v1):SELECT'
rc() { docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec -T redis-cache redis-cli --no-auth-warning --user ops --pass "$REDIS_OPS_PASSWORD" "$@"; }
k=engagement:live:1:20261013:QR_SCAN; [ "$(rc ACL DRYRUN svc_engagement INCR $k)" = OK ] || echo "FAIL: engagement cannot count"
for u in $(rc ACL USERS | grep '^svc_' | grep -vx svc_engagement); do [ "$(rc ACL DRYRUN "$u" INCR $k)" = OK ] && echo "FAIL: $u writes live counters"; done
```

The service's tests insert one `client_event_id` twice (one document, 200 with the original), void an event twice (409
`already_voided`, one `history` entry), and delete the live counter between two inserts (it then equals `engagement_daily.count`).

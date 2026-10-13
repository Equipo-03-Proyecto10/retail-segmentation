# ADR-0038 — MongoDB holds in-store engagement, owned by engagement-service alone

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The second delivery requires MongoDB in real operation: insert, query, update,
aggregation and indexes, demonstrated on data the system actually uses.
[ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md)
lifted ADR-0005's "design only" condition, but not the design itself.
[`../datastores/mongodb-design.md`](../datastores/mongodb-design.md) still
proposes six collections (`consumption_profile`, `rfm_snapshot`,
`segment_migration`, `clustering_model`, `recommendation`, `experiment_event`).
Every one of them now duplicates a fact that PostgreSQL holds and the web
reads: segment history and runs
([ADR-0017](0017-segment-assignment-history-replaces-the-mutable-current-segment.md)),
experiment events
([ADR-0019](0019-experiment-measurement-separates-assignment-exposure-and-conversion.md))
and the RN-34 consumption profile, which is computed from accepted sales.

The earlier draft of this record replaced them with one `customer_activity`
collection owned by an activity-service. It held four event types: QR scans,
GPS check-ins, recommendation clicks and purchases. Two of those four do not
belong in MongoDB:

- **Check-ins carry a rule that needs a join.** A check-in is verified against
  the store's location and geofence, and an override is an attributed,
  append-only review. That is check-ins-service's rule, its data lives in the
  `checkins` schema of PostgreSQL, next to `stores.store_location`
  ([ADR-0046](0046-service-owned-data-lives-in-service-schemas.md)), and the
  web can see it there.
- **Purchases are already recorded.** A sale is a `transaction` row with its
  `transaction_line` rows, written by the web's CSV path or by sales-service
  ([ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md)).
  A `PURCHASE` document would be a second copy of it, kept in step by a
  consumer, and the two would eventually disagree.

What is left is genuinely new data that no table holds: a product QR scanned on
the floor, and a tap on a recommendation shown to a customer. The Android client
produces both ([ADR-0041](0041-android-and-electron-clients-run-different-processes-over-different-formats.md)).
They are written far more often than they are read, read mostly as counts, never
joined transactionally with a sale, and their shape differs by type: a click
carries the label and the rank it was shown at, a scan does not.

[ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)
gives every fact one service writer.
[ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md)
gives every service one rule. A shared MongoDB database would break both.

Three things are judgement calls rather than forced:

- the MongoDB instance is a standalone `mongod`, so an event and its rollup
  cannot be written in one transaction;
- the built-in `readWrite` role is used rather than a custom role without
  `remove`;
- a "day" is a UTC calendar day.

Each is stated below with its cost.

## Decision

MongoDB holds exactly one application database, `engagement`. Only
engagement-service reads or writes it, as the MongoDB user `svc_engagement`,
defined in that database and holding `readWrite` on it and on nothing else. The
database has two collections. `engagement_event` records each `QR_SCAN` or
`RECO_CLICK` once per client event, under a `$jsonSchema` validator and a unique
`client_event_id`. An event is voided with a reason, never deleted.
`engagement_daily` is its rollup by store, day and type. Live counters in
`redis-cache` are a cache of that rollup and are rebuilt from it. Before it
writes, the service validates every identifier against three PostgreSQL contract
views: `api_products.product_ref_v1`, `api_stores.store_directory_v1` and
`api_customers.customer_ref_v1`. It holds no PostgreSQL table. Check-ins stay in
PostgreSQL ([ADR-0046](0046-service-owned-data-lives-in-service-schemas.md)),
and so do purchases.

### `engagement_event`

| Field | BSON type | Content |
|---|---|---|
| `_id` | string | The server-assigned `event_id` (UUID). It is the `{event_id}` of the API |
| `client_event_id` | string | A UUID generated on the device once per scan or tap. Unique |
| `type` | string | `QR_SCAN` or `RECO_CLICK` |
| `store_id` | int | `store.store_id`, checked in `api_stores.store_directory_v1` |
| `product_id` | int | `product.product_id`, checked in `api_products.product_ref_v1`. The QR payload is this id |
| `customer_id` | string, optional | `customer.customer_id` (UUID), checked in `api_customers.customer_ref_v1` |
| `label_code` | string, `RECO_CLICK` only | The segment label the recommendation was shown under, at most 40 characters as in `segment_label.label_code` |
| `rank` | int ≥ 1, `RECO_CLICK` only | The position the product was shown at |
| `recorded_by` | string | The token's `sub`: the `app_user.user_id` that made the call |
| `occurred_at` | date | The device's time of the scan or tap |
| `recorded_at` | date | The server's time of the insert |
| `voided` | bool | `false` on insert |
| `history` | array | One entry per change after insert: `{action: "VOIDED", reason, by, at}` |

**Validation.** The collection is created with `validationLevel: "strict"`,
`validationAction: "error"` and a `$jsonSchema` that:
- requires every field above except `customer_id`, `label_code` and `rank`;
- requires `label_code` and `rank` when `type` is `RECO_CLICK`, and forbids them
  when it is `QR_SCAN` (a `oneOf` over the two shapes);
- sets `additionalProperties: false`.

The last point keeps names, emails and coordinates out: an event carries
identifiers only.

**Indexes.**

| Index | Serves |
|---|---|
| `{client_event_id: 1}`, unique | Once per client event: a retry hits `E11000` instead of inserting twice |
| `{store_id: 1, occurred_at: -1}` | Events of one store, newest first |
| `{type: 1, occurred_at: -1}` | Events of one type, newest first |
| `{customer_id: 1, occurred_at: -1}` | Events of one customer, newest first |

### `engagement_daily`

One document per `{store_id, day, type}`, under a unique index on those three
fields, with a `count` of the events that are not voided. `day` is the UTC date
of `occurred_at`, as `YYYY-MM-DD`. An insert runs
`findOneAndUpdate({store_id, day, type}, {$inc: {count: 1}}, {upsert: true, returnDocument: "after"})`.
A void runs the same with `{$inc: {count: -1}}`.

### Operations

| Operation | Endpoint | MongoDB |
|---|---|---|
| Insert | `POST /v1/engagement-events` | `insertOne` into `engagement_event`, then the rollup `$inc`. On `E11000` for `client_event_id`, the service reads the stored event. If its `type`, `store_id` and `product_id` match the request, it answers 200 with the original event and writes nothing. If they differ, it answers 409 `client_event_conflict` |
| Query | `GET /v1/engagement-events` (`type`, `store_id`, `customer_id`, `voided`, `from`, `to`, `page`, `size`) and `GET /v1/engagement-events/{event_id}` | `find` with a range on `occurred_at`, sorted by `occurred_at: -1`. Each filter combination is served by one of the indexes above, and `explain("executionStats")` shows an `IXSCAN` on it |
| Update | `POST /v1/engagement-events/{event_id}/void`, ADMIN only, with a `reason` | `updateOne({_id, voided: false}, {$set: {voided: true}, $push: {history: {action: "VOIDED", reason, by, at}}})`, then the rollup `$inc: -1`. No match on an existing event means it is already voided: 409 `already_voided` |
| Aggregation | `GET /v1/engagement-summaries` (`from`, `to`, `store_id`, `group_by=store\|day\|type`) | `$match` on `day` and `store_id`, `$group` by the requested key with `$sum: "$count"`, `$sort` by that key, over `engagement_daily` |

The rollup is derived, so it is also rebuildable: a second pipeline over
`engagement_event` (`$match: {voided: false}`, then `$group` by `store_id`, `type` and the UTC
`$dateToString` of `occurred_at`) recomputes it, and a `$merge` into
`engagement_daily` repairs it. The rebuild is a maintenance command of the
service for a day range, not an endpoint, and the service's tests compare it
against the incremental counts.

### Live counters

`engagement:live:{store_id}:{yyyymmdd}:{type}` in `redis-cache`, TTL 48 h
([ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)).
After the rollup update, the service runs `INCR`, or `DECR` for a void. A result
of 1, or −1, means the key was missing, possibly evicted. The service then
overwrites it with the count that `findOneAndUpdate` returned, and sets the TTL.
A summary whose window is the current UTC day for one store reads these
counters. On a miss it reads `engagement_daily` and sets the key. Every other
summary runs the pipeline. If `redis-cache` is unreachable, the same answer
comes from `engagement_daily`, only slower.

### Write-time validation through PostgreSQL

`svc_engagement` holds SELECT on exactly three contract views and nothing else
in PostgreSQL
([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)):

| View | Check | Refusal |
|---|---|---|
| `api_stores.store_directory_v1` | The store exists and is active | 422 `unknown_store` |
| `api_products.product_ref_v1` | The product exists | 422 `unknown_product` |
| `api_customers.customer_ref_v1` | The customer exists, when `customer_id` is given | 422 `unknown_customer` |

`occurred_at` later than the server clock plus five minutes is refused with 422
`occurred_in_future`. `label_code` is checked against the pattern only.
engagement-service does not consume `api_segments.current_label_v1`. It records
which label the client showed, and does not decide whether it is current.

### Who creates the shape

The validator, the indexes and the `svc_engagement` user are created by
`services/engagement/mongo/init.js`. That script is idempotent, run in mongosh
with an administrator account, and applied before the service version that
needs it, the same expand-before-deploy order as `sql/01_schema.sql`. The
user's password comes from the instance's environment file, never from source. The
service never runs `collMod` or `createUser`. Its `/health/ready` lists the
collections and indexes and answers 503 `schema_not_ready` if one is missing.

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
- Every operation the course names is a real endpoint used in the
  demonstration: an insert on every scan and tap from Android, queries and
  voids from Electron, an aggregation on every summary.
- No fact lives in two stores. Check-ins and purchases have one home each, in
  PostgreSQL.
- A retried tap or scan is one event. The unique index decides that, not the
  client.
- Losing `redis-cache` loses nothing. Losing or corrupting `engagement_daily`
  loses nothing either: the rebuild pipeline recomputes it from the events.

**What this makes hard.**
- **No transaction across the event and its rollup.** A standalone `mongod`
  has no multi-document transactions. A crash between `insertOne` and the
  `$inc` leaves the rollup one short until the rebuild runs. Running a
  single-node replica set would fix that, at the cost of an oplog and a more
  involved infrastructure container. The team prefers a rebuildable rollup.
- **No referential integrity.** A product, store or customer is checked once,
  at write time. If the web later deletes a store or product, the events keep
  their ids. This service does not consume `stream:stores.v1.changed` or
  `stream:products.v1.changed`, so nothing tidies them. Summaries can show an
  id that no longer resolves, and Electron must render that as unknown, not
  fail.
- **`readWrite` can delete and drop.** The role includes `remove` and
  `dropCollection`. "Never deleted" rests on the service's code, a CI grep and
  review, not on the database.
- **A day is a UTC day.** The seeded stores are in Mexico, from Mérida to
  Tijuana, so an evening scan counts on the next UTC date. Store-local days
  would need each store's time zone, which no table holds.
- **The web cannot see any of this.** Engagement is service-only data. The
  end-to-end demonstration's step 5 ("the web shows it with no web change")
  must use a sale, not a scan. Engagement appears in Electron only.
- **The 4NF argument does not apply here.** The data model must say why the
  document shape is acceptable: one document per event, no repeating groups
  except `history`, which is bounded and always read with its event.
- **A new rule needs its RN id.** "Recorded once per client event, attributed,
  voided rather than deleted, and summarized" has no RN id yet.
  [ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md)
  requires one in `docs/business-rules.md` before the registry line cites it.
- **MongoDB costs memory.** It is the largest single infrastructure container
  in the budget of
  [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md),
  for a service with two collections.

**What must now be true elsewhere.**
- [`../datastores/mongodb-design.md`](../datastores/mongodb-design.md) is
  rewritten as this specification. Its six collections are withdrawn, with
  this record as the reason.
- `docs/data-model.md` documents the two collections, and the boundary that
  keeps them out of the relational model, with the shape justification above.
- `contracts/engagement/openapi.yaml` and `engagement.xsd` expose exactly the
  operations in the table, with the error codes named here
  ([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)).
- `deploy/infra/compose.yaml` runs MongoDB with authorization enabled, pinned
  to an exact MongoDB 7 image, with no published port
  ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)).
  Only `services/engagement/compose.yaml` sets `MONGO_URI`, and `.env.example`
  documents it with a local default.
- `sql/01_schema.sql` grants `svc_engagement` SELECT on the three views and
  nothing else. `contracts/db/grants.csv` lists exactly those three rows.
- The `svc_engagement` Redis ACL allows `~engagement:live:*` on `redis-cache`
  and the liveness selector on `redis-state`
  ([ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)).
- [ADR-0046](0046-service-owned-data-lives-in-service-schemas.md) holds the
  check-in tables. [ADR-0041](0041-android-and-electron-clients-run-different-processes-over-different-formats.md)
  sends exactly one `RECO_CLICK` per tap, with a fresh `client_event_id`.
- `services/engagement/mongo/seed.js` loads at least 30 events per type,
  referencing seeded stores, products and customers, so the query,
  `explain()` and aggregation have data before the first phone write. Its
  `client_event_id`s are fixed, so a second run inserts nothing, and it ends
  with the rollup rebuild. Seeded events are ordinary events and are counted.

## Compliance

In mongosh, connected as `svc_engagement` to the `engagement` database
(`docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec mongodb mongosh "$MONGO_URI"`):

```javascript
// One owner, one database: readWrite on engagement and nothing else.
db.runCommand({connectionStatus: 1, showPrivileges: true}).authInfo.authenticatedUserRoles
// expected: [ { role: "readWrite", db: "engagement" } ]

// Exactly the two collections, the validator and the indexes.
db.getCollectionNames().sort()                       // [ "engagement_daily", "engagement_event" ]
db.getCollectionInfos({name: "engagement_event"})[0].options.validator.$jsonSchema !== undefined
db.engagement_event.getIndexes().map(i => [i.key, !!i.unique])
// includes [{client_event_id:1}, true], [{store_id:1, occurred_at:-1}, false],
//          [{type:1, occurred_at:-1}, false], [{customer_id:1, occurred_at:-1}, false]
db.engagement_daily.getIndexes().some(i => i.unique && i.key.store_id === 1 && i.key.day === 1 && i.key.type === 1)

// The validator refuses a QR_SCAN that carries a rank.
db.engagement_event.insertOne({_id: "x", client_event_id: "y", type: "QR_SCAN", store_id: 1,
  product_id: 1, rank: 1, recorded_by: "z", occurred_at: new Date(), recorded_at: new Date(),
  voided: false, history: []})                       // expected: MongoServerError "Document failed validation"

// The query uses its index.
const p = db.engagement_event.find({store_id: 1, occurred_at: {$gte: ISODate("2026-10-01")}})
  .sort({occurred_at: -1}).explain("executionStats")
JSON.stringify(p.queryPlanner.winningPlan).includes('"indexName":"store_id_1_occurred_at_-1"')  // true
p.executionStats.totalDocsExamined === p.executionStats.nReturned                              // true

// The rollup agrees with the events.
const rebuilt = db.engagement_event.aggregate([
  {$match: {voided: false}},
  {$group: {_id: {store_id: "$store_id", type: "$type",
            day: {$dateToString: {format: "%Y-%m-%d", date: "$occurred_at", timezone: "UTC"}}},
            count: {$sum: 1}}}]).toArray()
rebuilt.filter(r => (db.engagement_daily.findOne({store_id: r._id.store_id, day: r._id.day,
  type: r._id.type}) || {count: -1}).count !== r.count).length                               // 0
db.engagement_daily.countDocuments({count: {$gt: 0}}) === rebuilt.length                       // true
```

CI runs these from the repository root, and each must print nothing:

```bash
# Only engagement-service configures or drives MongoDB.
grep -rlnE 'MONGO_URI|pymongo|motor|mongodb' services/ packages/ bench/ \
  | grep -vE '^services/engagement/'
grep -rnE '^\s*mongo(db)?:' services/*/compose.yaml

# Events are voided, never deleted, and the service never changes the shape.
grep -rnE '\b(delete_one|delete_many|find_one_and_delete|drop_collection|drop_database|collMod|createUser)\b|\.drop\(' \
  services/engagement/src/

# PostgreSQL: three views, SELECT only, and no table.
psql -d retail -Atc "SELECT table_schema||'.'||table_name||':'||privilege_type
  FROM information_schema.role_table_grants WHERE grantee = 'svc_engagement'
  ORDER BY 1" \
  | grep -vxE 'api_(products\.product_ref_v1|stores\.store_directory_v1|customers\.customer_ref_v1):SELECT'
```

```bash
# Live counters: only engagement writes engagement:live:*, and only on redis-cache.
rc() { docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec -T redis-cache redis-cli --no-auth-warning --user ops --pass "$REDIS_OPS_PASSWORD" "$@"; }
[ "$(rc ACL DRYRUN svc_engagement INCR engagement:live:1:20261013:QR_SCAN)" = OK ] || echo "FAIL: engagement cannot count"
for u in $(rc ACL USERS | grep '^svc_' | grep -vx svc_engagement); do
  [ "$(rc ACL DRYRUN "$u" INCR engagement:live:1:20261013:QR_SCAN)" = OK ] && echo "FAIL: $u may write live counters"
done
```

The service's own tests insert the same `client_event_id` twice and assert one
document and a 200 with the original. They void an event twice and assert 409
`already_voided` and a single `history` entry. They delete the live counter
between two inserts and assert that the counter afterwards equals
`engagement_daily.count`.

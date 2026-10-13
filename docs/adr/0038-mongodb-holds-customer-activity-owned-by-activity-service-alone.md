# ADR-0038 — MongoDB holds customer activity, owned by activity-service alone

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The second delivery requires MongoDB as "real storage, with insert, query,
update, aggregation and indexing demonstrated".
[`../datastores/mongodb-design.md`](../datastores/mongodb-design.md) proposed
six collections. It was written before ADR-0017 and ADR-0019 moved segment
history, runs, recommendations and experiments into PostgreSQL, where they
join, are constrained and are audited. Copying them to MongoDB now would create
two sources of truth for the same facts.

The clients produce data that suits a document store and fits no existing table:

- QR scans of products;
- GPS check-ins at stores, with coordinates and accuracy;
- recommendation clicks;
- the purchases those lead to.

These events vary in shape by type. They are written far more often than
they are read, are read mostly as aggregates, and are never joined
transactionally with a sale.

## Decision

MongoDB holds exactly one database, `mosaiq_activity`, with one collection,
`customer_activity`. Only activity-service reads or writes it, as the MongoDB
user `svc_activity`, whose rights are limited to that database.

**Documents.** Each document carries:

| Field | Content |
|---|---|
| `type` | `QR_SCAN`, `STORE_CHECKIN`, `RECO_CLICK` or `PURCHASE` |
| `customer_id` | optional |
| `store_id` | |
| `product_id` | optional |
| `location` | optional GeoJSON `Point`, plus `accuracy_m` |
| `verified` | for check-ins |
| `source` | `{client, user_id, transaction_id?}` |
| `occurred_at` | |
| `recorded_at` | |
| `history` | each change to `verified`, with who changed it and when |

**Validation.** A `$jsonSchema` validator enforces the required fields per
`type`.

**Indexes.**
- `{store_id: 1, occurred_at: -1}`
- `{customer_id: 1, occurred_at: -1}`
- `{type: 1, occurred_at: -1}`
- a `2dsphere` index on `location`
- a unique, partial index on `source.transaction_id` where it exists, which
  makes the `sales.recorded` consumer idempotent

**Operations demonstrated.**
- insert: recording an activity;
- query: filtered lists, with `explain()` proving index use;
- update: verifying or unverifying a check-in, appended to `history`;
- aggregation: counts by store, day and type through `$match`/`$group`/`$sort`.

Anything that must join, be constrained or be audited stays in PostgreSQL. A
customer or store referenced here is a plain identifier, with no cross-store
integrity, and the service validates it against PostgreSQL on write.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| The six collections of the earlier design (`consumption_profile`, `rfm_snapshot`, `segment_migration`, `clustering_model`, `recommendation`, `experiment_event`) | Each duplicates a PostgreSQL table that ADR-0017 or ADR-0019 made authoritative. Two copies of segment history disagree eventually, and nothing would decide which one is right |
| Ingestion rejections and model telemetry (the pre-reset store split) | Rejections are already a PostgreSQL table (`sales_load_rejection`) shown by the web. Telemetry has no consumer in this delivery |
| Several services share MongoDB | Recreates the ungoverned shared store that ADR-0034's grants avoid for PostgreSQL. One owner keeps the rules in one place |
| Store activity in PostgreSQL too, and skip MongoDB | Meets no requirement. The course mandates MongoDB in real operation |

## Consequences

**What this makes easy.**
- The MongoDB requirement is met by data the clients generate during the
  demonstration, not by a staged copy.
- Every operation the course names maps to a real endpoint.
- No fact lives in two stores.

**What this makes hard.**
- **No foreign keys.** A deleted customer leaves orphaned activity, which is
  acceptable for an event log and must be stated in the data model.
- **Geo-verification needs store coordinates.** PostgreSQL does not have them
  yet, so F14-17 adds `latitude`/`longitude` to `store`.
- **The 4NF justification does not apply to MongoDB.** The data model must say
  why the document shape is acceptable there.

**What must now be true elsewhere.**
- [`../datastores/mongodb-design.md`](../datastores/mongodb-design.md) is
  rewritten as this specification (F13-06).
- `docs/data-model.md` documents the collection, and the boundary that keeps it
  out of the relational model.
- The activity contract (F14-15) exposes exactly these operations.

## Compliance

```javascript
// In mongosh as an administrator: one database, one collection, the indexes above.
db.getSiblingDB("mosaiq_activity").getCollectionNames()          // ["customer_activity"]
db.getSiblingDB("mosaiq_activity").customer_activity.getIndexes() // includes 2dsphere and unique source.transaction_id
db.getSiblingDB("mosaiq_activity").getCollectionInfos()[0].options.validator // $jsonSchema present
```

```bash
# Only activity-service has a MongoDB driver.
grep -rlE 'pymongo|mongodb' services/ --include='*.py' --include='*.toml' --include='*.txt' \
  | grep -v '^services/activity-service/' && echo "MongoDB used outside activity-service" || true
```

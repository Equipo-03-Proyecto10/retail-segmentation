# MongoDB — specification

The specification of [ADR-0038](../adr/0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md),
which governs where this document and the record disagree.
[ADR-0040](../adr/0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)
runs the server, and [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)
holds the live counters.

**Status.** F13-06 (#428) builds the server in
[`deploy/infra/`](../../deploy/infra/README.md). It holds no application
database yet. engagement-service's own story, F14-24 (#466), adds the
database, its user, its collections and its seed in
`services/engagement/mongo/`.

MongoDB holds one thing: in-store engagement that no PostgreSQL table holds, a
product QR scanned on the floor and a tap on a shown recommendation. Check-ins
and purchases stay in PostgreSQL. Only engagement-service reads or writes it;
the web and every other service never connect.

## Server

| | `mongodb` |
|---|---|
| Image | `mongo:7.0.41`, pinned by its multi-architecture digest in `deploy/infra/compose.yaml` |
| Topology | One standalone `mongod`, no replica set |
| Authorization | `--auth`: a client without credentials is refused `listDatabases` and every read or write |
| WiredTiger cache | `--wiredTigerCacheSizeGB 0.25` |
| Container `mem_limit` | 512 MB, the largest infrastructure container in ADR-0040's 3,000 MB budget |
| Network | The external network `mosaiq` only; no published port |
| Storage | The named volume `mongodb-data` at `/data/db` |
| Hardening | The image's `mongodb` user (999:999), no capabilities, `no-new-privileges`, a read-only root filesystem |

Changing the tag is a pull request of its own.

## Accounts

| User | Defined in | Roles | Used by |
|---|---|---|---|
| root | `admin` | `root` | Administrators only. Created on the first start from `/etc/mosaiq/mongodb/root.env`, which never enters a repository |
| `svc_engagement` | `engagement` | `readWrite` on `engagement` and nothing else | engagement-service, through `MONGO_URI`, which only engagement's `compose.yaml` sets |

`MONGO_URI` has the form
`mongodb://svc_engagement:<password>@mongodb:27017/engagement?authSource=engagement`.
`services/engagement/mongo/init.js` creates the user with the password it
reads from `MONGO_ENGAGEMENT_PASSWORD`; `.env.example` has a local default for
both.

No other application database and no other user exist. A second service
cannot be given a collection here: that would recreate the shared store that
the per-boundary grants avoid in PostgreSQL.

## The database `engagement`

### `engagement_event`

One document per `QR_SCAN` or `RECO_CLICK`, recorded once per client event
(RN-55).

| Field | Type | Meaning |
|---|---|---|
| `_id` | string, UUID | The server-assigned `event_id`, the API's `{event_id}` |
| `client_event_id` | string, UUID | Generated on the device once per scan or tap. Unique |
| `type` | string | `QR_SCAN` or `RECO_CLICK` |
| `store_id` | int | The store where it happened |
| `product_id` | int | The product scanned or shown; a QR payload is the `product_id` |
| `customer_id` | string, UUID | Optional: the customer the event concerns |
| `label_code` | string, at most 40 characters | `RECO_CLICK` only: the segment label the recommendation was shown under |
| `rank` | int ≥ 1 | `RECO_CLICK` only: the position shown |
| `recorded_by` | string | The token's `sub` |
| `occurred_at` | date | The device's time |
| `recorded_at` | date | The server's time |
| `voided` | bool | `false` on insert |
| `history` | array | One `{action: "VOIDED", reason, by, at}` per change |

The validator is a strict `$jsonSchema` with validation action `error` and
`additionalProperties: false`, and a `oneOf` over the two shapes. A scan never
carries a label or a rank, and names, emails and coordinates have nowhere to
go.

| Index | Serves |
|---|---|
| `{client_event_id: 1}`, unique | A retried scan or tap hits `E11000` instead of inserting twice |
| `{store_id: 1, occurred_at: -1}` | Events of one store, newest first |
| `{type: 1, occurred_at: -1}` | Events of one type, newest first |
| `{customer_id: 1, occurred_at: -1}` | Events about one customer, newest first |

An event is voided with a reason, never deleted.

### `engagement_daily`

The rollup: one document per store, UTC day and type.

| Field | Type | Meaning |
|---|---|---|
| `store_id` | int | The store |
| `day` | string, `YYYY-MM-DD` | The UTC calendar day of `occurred_at` |
| `type` | string | `QR_SCAN` or `RECO_CLICK` |
| `count` | int | Events of that store, day and type that are not voided |

A unique index on `{store_id: 1, day: 1, type: 1}` keeps one document per key.
The rollup is derived and rebuildable: losing it, or `redis-cache`, loses
nothing.

## Operations

| Operation | Endpoint | MongoDB |
|---|---|---|
| Insert | `POST /v1/engagement-events` | `insertOne`, then the rollup `$inc`. On `E11000`, a stored event with the same `type`, `store_id` and `product_id` answers 200 with the original and writes nothing; otherwise 409 `client_event_conflict` |
| Query | `GET /v1/engagement-events` (`type`, `store_id`, `customer_id`, `voided`, `from`, `to`, `page`, `size`) and `GET /v1/engagement-events/{event_id}` | `find` with a range on `occurred_at`, sorted `occurred_at: -1`, each filter served by one index (`IXSCAN` in `explain`) |
| Update | `POST /v1/engagement-events/{event_id}/void`, `ADMIN` only, with a `reason` | `updateOne({_id, voided: false}, {$set: {voided: true}, $push: {history: …}})`, then `$inc: -1`. Already voided: 409 `already_voided` |
| Aggregation | `GET /v1/engagement-summaries` (`from`, `to`, `store_id`, `group_by=store\|day\|type`) | `$match` on `day` and `store_id`, `$group` with `$sum: "$count"`, `$sort`, over `engagement_daily` |

The rollup's `$inc` is a `findOneAndUpdate` with `upsert: true` and
`returnDocument: "after"`: `+1` on an insert, `-1` on a void.

A maintenance command, never an endpoint, rebuilds the rollup for a day range:
`$match: {voided: false}`, `$group` by store, type and UTC day over
`engagement_event`, and `$merge` into `engagement_daily`.

### Live counters

`engagement:live:{store_id}:{yyyymmdd}:{type}` on `redis-cache`, 48 h TTL
(ADR-0037). After the rollup update the service runs `INCR`, or `DECR` for a
void. A result of 1, or −1, means the key was missing, so the service
overwrites it with the count `findOneAndUpdate` returned. A one-store summary
of the current UTC day reads the counter, and falls back to `engagement_daily`
on a miss or when `redis-cache` is unreachable. Every other summary runs the
pipeline.

## Write-time validation

`svc_engagement` holds SELECT on exactly three PostgreSQL views and nothing
else: `api_products.product_ref_v1`, `api_stores.store_directory_v1` and
`api_customers.customer_ref_v1` (ADR-0044). Before it writes, the service
refuses:
- 422 `unknown_store`: the store must exist and be active;
- 422 `unknown_product`: the product must exist;
- 422 `unknown_customer`: when a customer is given, it must exist;
- 422 `occurred_in_future`: `occurred_at` more than five minutes ahead.

`label_code` is checked against its pattern only, so the label vocabulary stays
segments'. Ids are checked once, at write time: a store or product the web
later deletes keeps its events, and Electron renders an id that no longer
resolves as unknown.

## Who creates the shape

`services/engagement/mongo/init.js` creates the database, the user, both
collections, the validator and the indexes. It is idempotent, and an
administrator runs it in mongosh as root before the engagement version that
needs it, as a schema section is applied before its service. The service never
runs `collMod` or `createUser`; its `/health/ready` answers 503
`schema_not_ready` while a collection or an index is missing.

`services/engagement/mongo/seed.js` loads at least 30 events per type over
seeded stores, products and customers, with fixed `client_event_id`s so that a
second run inserts nothing, and ends with the rollup rebuild.

## Judgement calls

| Call | Cost |
|---|---|
| A standalone `mongod` | An event and its rollup are not written in one transaction. A crash between `insertOne` and the `$inc` leaves the rollup one short until the rebuild runs. A single-node replica set would fix that, at the cost of an oplog |
| The built-in `readWrite` role | It includes `remove` and `drop`. "Never deleted" rests on the service's code, a CI grep and review (accepted exception E-18), because a custom role without `remove` would fail the rebuild's `$merge` at run time |
| A day is a UTC day | The seeded stores run from Mérida to Tijuana, so an evening scan counts on the next UTC date. Store-local days would need each store's time zone, which no table holds |

## Failure behaviour

| Failure | Behaviour |
|---|---|
| MongoDB down | engagement's event operations and summaries answer 503 `database_unavailable`, never 500, and its `/health/ready` answers 503. No other service notices |
| `redis-cache` unreachable | A one-store summary of today falls back to `engagement_daily` |
| MongoDB restarted | Nothing is lost: events and the rollup live in the `mongodb-data` volume |
| A rollup that disagrees with its events | The rebuild recomputes it from `engagement_event` |

## Checks

`deploy/infra/compose.yaml` is checked by the `Infrastructure` workflow on
every change under `deploy/infra/`:
- the image is pinned to an exact `7.0` patch and its digest;
- the server publishes no port;
- a client on the `mosaiq` network without credentials has `listDatabases`
  refused;
- as root, the running server shows authorization on, a 0.25 GB cache and no
  database beyond MongoDB's own.

ADR-0038's compliance block is the check once engagement is built: the user's
only role, the two collections, the validator, the four indexes, a refused
invalid document, index use in `explain`, and a rollup equal to its rebuild. Its
greps keep MongoDB out of every other service, and delete calls out of
engagement's source.

## Withdrawn design

This document used to propose six collections for the analytics modules:
`consumption_profile`, `rfm_snapshot`, `segment_migration`, `clustering_model`,
`recommendation` and `experiment_event`. ADR-0038 withdrew all six, because
each copies a fact PostgreSQL already holds and the web already shows (segment
history and runs, ADR-0017; experiment events, ADR-0019; the RN-34 consumption
profile). Two copies of segment history would disagree eventually, and nothing
would decide which is right. The earlier text is in the history of this file,
as of commit `8ea5436`.

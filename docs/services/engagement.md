# engagement

> **Status:** Planned (phase 14, core). ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** In-store engagement (`QR_SCAN`, `RECO_CLICK`, `STORE_VISIT`, `RECO_IMPRESSION`, `CAMPAIGN_EXPOSURE`, `CART_ADD`, `CART_REMOVE`, `NAVIGATION`) is recorded once per client event as a distinct fact that is never a sale, attributed, voided rather than deleted, and summarized (RN-55)
>
> **Contract:** `/v1` is frozen and records `QR_SCAN` and `RECO_CLICK`. `/v2` (engagement 2.1.0, `engagement.v2.xsd`) keeps both with their meaning and adds the six other kinds. The service serves both since 0.2.0 (#799); a `/v2` kind is invisible to `/v1`.

## Summary

| Field | Value |
|---|---|
| Language | Flask + MongoDB |
| Port (loopback) | 5012 |
| Public path | `/api/engagement/v1/` |
| Wave | core |
| PostgreSQL role | `svc_engagement` (no table of its own) |
| Redis ACL user | `svc_engagement`, same user line on `redis-state` and `redis-cache` |
| MongoDB | Database `engagement`, user `svc_engagement` (`readWrite` on it only) |
| Compose project / image | `mosaiq-engagement` / `ghcr.io/equipo-03-proyecto10/mosaiq-engagement` |
| Used by | Android: `QR_SCAN` after each decoded scan, one `RECO_CLICK` per tap (F17-02, F17-05). Electron: summaries and voids (F16-04). Experimentation and analytics-kpis: the interaction facts, through the stream decided in ADR-0075, never through this service's API. |
| Governing ADRs | [ADR-0038](../adr/0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md), [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) |

## Business rule

- Each event is recorded once per `client_event_id` (unique index; a retry hits `E11000`), attributed to the token's `sub`, and never deleted: a void adds a history entry. Summaries count only events that are not voided.
- Before writing, the service validates store, product and customer against three published views. engagement is the only owner of the recommendation-click fact. Check-ins and purchases stay in PostgreSQL.
- There are eight kinds of event, each a distinct fact (taxonomy of #798, `/v2`). Every kind carries `client_event_id`, `type`, `store_id` and `occurred_at`; the rest is per kind:

  | Kind | Also required | Meaning |
  |---|---|---|
  | `QR_SCAN` | `product_id` | A product QR decoded on the floor |
  | `RECO_CLICK` | `product_id`, `label_code`, `rank` | A tap on a shown recommendation |
  | `RECO_IMPRESSION` | `product_id`, `label_code`, `rank` | A recommendation shown on screen, before any tap |
  | `STORE_VISIT` | none | A customer or visitor arriving at a store |
  | `CAMPAIGN_EXPOSURE` | `campaign_id`, `customer_id` | A customer shown a campaign |
  | `CART_ADD`, `CART_REMOVE` | `product_id`, `quantity` | A product added to or removed from a cart |
  | `NAVIGATION` | `screen` | A screen opened in a client |

- No event is a sale. A cart is intent, an exposure is not a conversion, and a confirmed transaction exists only in sales-service. A summary counts events that are not voided and gives each kind its own key.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| POST | `/v1/engagement-events` | Record a `QR_SCAN` or `RECO_CLICK` (`client_event_id`, store, product; `label_code` and `rank` for a click). Frozen | ADMIN, STORE_MANAGER |
| GET | `/v1/engagement-events` | List events (`type`, `store_id`, `customer_id`, `voided`, `from`, `to`, `page`, `size`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| GET | `/v1/engagement-events/{event_id}` | One event | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| POST | `/v1/engagement-events/{event_id}/void` | Void with a reason | ADMIN |
| GET | `/v1/engagement-summaries` | Counts by store, day or type (`from`, `to`, `store_id`, `group_by`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, INVENTORY_PLANNER, AUDITOR |

`/v2` has the same five operations with the same roles, over all eight kinds:

| Method | Path | Purpose |
|---|---|---|
| POST | `/v2/engagement-events` | Record any of the eight kinds, one shape per `type` |
| GET | `/v2/engagement-events` | List events (`type` may be any of the eight) |
| GET | `/v2/engagement-events/{event_id}` | One event |
| POST | `/v2/engagement-events/{event_id}/void` | Void with a reason |
| GET | `/v2/engagement-summaries` | Counts by store, day or type, every kind under its own key |

`GET /schema.v2.xsd` serves `engagement.v2.xsd` and is already served.

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `engagement_event` | MongoDB collection | insert, query, update (void, relay `$pull`) | Strict `$jsonSchema` validator, one shape per kind, plus the outbox field `relay_pending` (ADR-0075); unique `client_event_id`; three secondary indexes and the relay's partial index `relay_pending_occurred_at_1` |
| `engagement_daily` | MongoDB collection | `$inc` upsert, aggregation | Rollup by store, UTC day and type; rebuildable by a maintenance `$merge` |
| PostgreSQL | — | No table | SELECT on three published views |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Consumes | `api_products.product_ref_v1` | products |
| Consumes | `api_stores.store_directory_v1` | stores |
| Consumes | `api_customers.customer_ref_v1` | customers |

## Events

ADR-0075 (accepted, issue #948) decides how the interaction facts leave this service, and #799 implements it. The stream's contract is [`contracts/streams/engagement.interaction_recorded.v1.md`](../../contracts/streams/engagement.interaction_recorded.v1.md). engagement relays them on one stream, `stream:engagement.v1.interaction_recorded`, from an outbox kept on the `engagement_event` document itself, because a standalone `mongod` cannot write an event and a second document atomically. The facts carried are `RECO_IMPRESSION`, `QR_SCAN`, `RECO_CLICK` and `CAMPAIGN_EXPOSURE`; a void is a later `VOIDED` notice. Delivery is at least once and consumers deduplicate by the entry's `event_id`.

experimentation consumes a `CAMPAIGN_EXPOSURE` (the campaign and the customer) from that stream into its own projection, and counts exposures per campaign and customer from its own schema. It does not call this service and reads none of its stores (ADR-0044). The count is eventually consistent, and a voided exposure stops counting when the `VOIDED` notice arrives. analytics-kpis never consumes the stream: it reads the owners' `api_*` views (ADR-0059).

The relay is a thread of the service's container. Every second it reads up to 100 events with a notice pending, through the partial index, publishes them with the kit's `StreamPublisher` (`XADD`, then `WAITAOF 1 0 2000`, kit-py 0.1.8) and only then `$pull`s them. The Redis ACL line grants `+waitaof` and `+xadd` on this one stream. A relay or `redis-state` outage leaves the notices on the documents until it returns.

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| cache | `engagement:live:{store_id}:{yyyymmdd}:{type}` | 48 h | Live counters, rebuilt from `engagement_daily` on a miss |
| state | `engagement:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |
| state | `stream:engagement.v1.interaction_recorded` | none; `MAXLEN ~ 10000` | Interaction facts for experimentation and recommendations (ADR-0075); `XADD` by the relay only |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 409 | `client_event_conflict` | The same `client_event_id` with a different type, store or product |
| 409 | `already_voided` | The event is already voided |
| 422 | `unknown_store` | The store must exist and be active |
| 422 | `unknown_product`, `unknown_customer` | A reference does not exist |
| 422 | `occurred_in_future` | More than five minutes ahead of the server |
| 404 | `event_not_found` | An unknown `{event_id}` on a read or a void |
| 422 | `invalid_query` | `to` before `from`, or an unknown `group_by`; `details` names the parameter |
| 503 | `schema_not_ready` | `/health/ready` while a collection or index is missing |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-18 — "never deleted" is enforced in service code, not in MongoDB

- **What:** The MongoDB user holds the built-in `readWrite` role, which includes `remove`. Events are voided, never deleted, by convention in the service.
- **Why it is acceptable:** A custom role without `remove` would fail the rollup rebuild and the start-up index checks with authorization errors. The boundary that matters for independence is one user on one database.
- **Guard:** A CI grep forbids delete calls in the service. The ADR names this the weakest point of the decision and revisits it if MongoDB ever holds anything sensitive.
- **Decided in:** [ADR-0038](../adr/0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md) (Alternatives considered). Register: [E-18](./README.md#e-18--never-deleted-is-enforced-in-service-code-not-in-mongodb).

### E-19 — one PostgreSQL, one DDL file, one redis-state, one MongoDB

- **What:** All services share one PostgreSQL database, one DDL file, one `redis-state`, one `redis-cache` and one MongoDB server. Independence is by role, schema and prefix, not by instance.
- **Why it is acceptable:** The course requires one DDL file that runs clean from empty on one 8 GB VM, with no managed database, and the web must see what services write.
- **Guard:** One `svc_*` role per service, one ACL user per service limited to its prefixes, one MongoDB user on one database, and a memory budget CI checks. Each boundary's schema section is idempotent and deploys expand-before-deploy.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Alternatives considered); [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) (Instances); [ADR-0040](../adr/0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md) (Decision). Register: [E-19](./README.md#e-19--one-postgresql-one-ddl-file-one-redis-state-one-mongodb).

### E-20 — the liveness key and the JWKS file are read by every service

- **What:** Every service reads `pub:auth:v1:live:{sid}` and the mounted `/run/mosaiq/jwks.json`, both published by auth.
- **Why it is acceptable:** Authentication must be checked on every request, locally, without HTTP between services.
- **Guard:** The Redis selector allows `EXISTS` on that one family and nothing else of `auth:*`. Both are versioned contracts (`contracts/auth/redis-liveness.v1.md`). Redis failure fails closed.
- **Decided in:** [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md) (Verification in every service); [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) (ACLs). Register: [E-20](./README.md#e-20--the-liveness-key-and-the-jwks-file-are-read-by-every-service).

### E-21 — a mechanism-only kit is shared by every service

- **What:** Python services pin `mosaiq-kit` and Rust services pin `kit-rs` by tag: token verification, negotiation, errors, pagination, health, relay and consumer helpers.
- **Why it is acceptable:** Re-implementing security and negotiation fifteen times would be worse for independence and safety than one pinned, versioned mechanism.
- **Guard:** A kit holds no permission names, no rule and no Redis key except the liveness family. Each service pins an exact tag and upgrades in its own pull request, and path dependencies are rejected.
- **Decided in:** [ADR-0045](../adr/0045-services-never-import-the-web-system.md) (Decision). Register: [E-21](./README.md#e-21--a-mechanism-only-kit-is-shared-by-every-service).

## Failure behaviour

| Dependency down | Effect |
|---|---|
| PostgreSQL unreachable | `/health/ready` reports not ready. `POST /v1/engagement-events` answers 503 `database_unavailable`, never 500, because it validates store, product and customer against the three published views; reads, voids and summaries use MongoDB only. `/health/live` stays 200 |
| MongoDB down | Event operations and summaries that reach MongoDB answer 503 `database_unavailable`, never 500 (ADR-0035); `/health/ready` is 503. Other services are unaffected |
| `redis-cache` down | A one-store summary of today falls back to `engagement_daily` |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone. The relay keeps the notices pending and publishes them when it returns |
| Any other service down | No effect: services never call each other over HTTP |

## Operations

| Task | Command | Notes |
|---|---|---|
| Create the database, user, validator and indexes | `mongo/init.js` in mongosh as root, with `MONGO_ENGAGEMENT_PASSWORD` set | Before the service version that needs it; idempotent, and a later run brings an older validator and the index set back to the definition. The service never creates or alters any of it, and `/health/ready` is 503 until it exists. **0.2.0 needs it re-applied first** (eight shapes, `relay_pending`, the relay's partial index): its readiness is 503 against the older validator, which would refuse its inserts |
| Load demonstration data | `mongo/seed.js` in mongosh | 40 `QR_SCAN` and 36 `RECO_CLICK` over stores 1-6, products 1-40 and customers 1-30 on ten fixed UTC days; fixed ids, so a second run inserts nothing; ends by rebuilding `engagement_daily` for those days |
| Rebuild the rollup | `python -m app rebuild-rollup --from YYYY-MM-DD --to YYYY-MM-DD` in the service's container | Recomputes `engagement_daily` from the events that are not voided for the UTC days, inclusive, and sets the live counters still alive to the rebuilt counts. A crash between an insert and its `$inc` is repaired by it. Not an endpoint |
| Re-queue the interaction stream | `python -m app requeue-relay --from YYYY-MM-DD --to YYYY-MM-DD` in the service's container | Sets `relay_pending` again on every relayed event of the UTC days, inclusive (`RECORDED`, and `VOIDED` when voided), so the relay publishes them again: for a consumer that fell behind the stream's retention or joined later (ADR-0075). Consumers dedupe by `event_id`. Not an endpoint |

The first request after a counter on `redis-cache` expires or is lost repairs it: the `INCR` or `DECR` returns 1 or -1, so the counter is overwritten with the count the rollup update returned.

## Compliance

- [ADR-0038](../adr/0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md) Compliance (`explain()` shows index use; one MongoDB owner; the user's roles).
- [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) Compliance (`engagement:live:*` on `redis-cache`).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-23 contract, F14-24 implementation; consumed by F17-02, F17-05, F16-04. Issue numbers live in [`docs/backlog.md`](../backlog.md).

# engagement

> **Status:** Planned (phase 14, core). ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** In-store engagement (`QR_SCAN`, `RECO_CLICK`) is recorded once per client event, attributed, voided rather than deleted, and summarized (RN-55)

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
| Used by | Android: `QR_SCAN` after each decoded scan, one `RECO_CLICK` per tap (F17-02, F17-05). Electron: summaries and voids (F16-04). |
| Governing ADRs | [ADR-0038](../adr/0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md), [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) |

## Business rule

- Each event is recorded once per `client_event_id` (unique index; a retry hits `E11000`), attributed to the token's `sub`, and never deleted: a void adds a history entry. Summaries count only events that are not voided.
- Before writing, the service validates store, product and customer against three published views. engagement is the only owner of the recommendation-click fact. Check-ins and purchases stay in PostgreSQL.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| POST | `/v1/engagement-events` | Record a `QR_SCAN` or `RECO_CLICK` (`client_event_id`, store, product; `label_code` and `rank` for a click) | ADMIN, STORE_MANAGER |
| GET | `/v1/engagement-events` | List events (`type`, `store_id`, `customer_id`, `voided`, `from`, `to`, `page`, `size`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| GET | `/v1/engagement-events/{event_id}` | One event | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| POST | `/v1/engagement-events/{event_id}/void` | Void with a reason | ADMIN |
| GET | `/v1/engagement-summaries` | Counts by store, day or type (`from`, `to`, `store_id`, `group_by`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, INVENTORY_PLANNER, AUDITOR |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `engagement_event` | MongoDB collection | insert, query, update (void) | Strict `$jsonSchema` validator; unique `client_event_id`; three secondary indexes |
| `engagement_daily` | MongoDB collection | `$inc` upsert, aggregation | Rollup by store, UTC day and type; rebuildable by a maintenance `$merge` |
| PostgreSQL | — | No table | SELECT on three published views |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Consumes | `api_products.product_ref_v1` | products |
| Consumes | `api_stores.store_directory_v1` | stores |
| Consumes | `api_customers.customer_ref_v1` | customers |

## Events

None.

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| cache | `engagement:live:{store_id}:{yyyymmdd}:{type}` | 48 h | Live counters, rebuilt from `engagement_daily` on a miss |
| state | `engagement:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

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
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| Any other service down | No effect: services never call each other over HTTP |

## Operations

| Task | Command | Notes |
|---|---|---|
| Create the database, user, validator and indexes | `mongo/init.js` in mongosh as root, with `MONGO_ENGAGEMENT_PASSWORD` set | Before the service version that needs it; idempotent, and a later run brings an older validator and the index set back to the definition. The service never creates or alters any of it, and `/health/ready` is 503 until it exists |
| Load demonstration data | `mongo/seed.js` in mongosh | 40 `QR_SCAN` and 36 `RECO_CLICK` over stores 1-6, products 1-40 and customers 1-30 on ten fixed UTC days; fixed ids, so a second run inserts nothing; ends by rebuilding `engagement_daily` for those days |
| Rebuild the rollup | `python -m app rebuild-rollup --from YYYY-MM-DD --to YYYY-MM-DD` in the service's container | Recomputes `engagement_daily` from the events that are not voided for the UTC days, inclusive, and sets the live counters still alive to the rebuilt counts. A crash between an insert and its `$inc` is repaired by it. Not an endpoint |

The first request after a counter on `redis-cache` expires or is lost repairs it: the `INCR` or `DECR` returns 1 or -1, so the counter is overwritten with the count the rollup update returned.

## Compliance

- [ADR-0038](../adr/0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md) Compliance (`explain()` shows index use; one MongoDB owner; the user's roles).
- [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) Compliance (`engagement:live:*` on `redis-cache`).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-23 contract, F14-24 implementation; consumed by F17-02, F17-05, F16-04. Issue numbers live in [`docs/backlog.md`](../backlog.md).

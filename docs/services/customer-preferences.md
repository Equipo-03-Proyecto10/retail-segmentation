# customer-preferences

> **Status:** Planned (phase 14, core). Not built until ADR-0034 to ADR-0046 are accepted (F13-01, #408).
> **One rule:** A customer's preferred channels and interest categories are each replaced only atomically, as a whole set of existing references (RN-54)

## Summary

| Field | Value |
|---|---|
| Language | Rust (axum) |
| Port (loopback) | 5008 |
| Public path | `/api/customer-preferences/v1/` |
| Wave | core |
| PostgreSQL role | `svc_preferences` |
| Redis ACL user | `svc_preferences`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-customer-preferences` / `ghcr.io/equipo-03-proyecto10/mosaiq-customer-preferences` |
| Used by | Android: interest capture (F17-04). Electron: both preference sets (F16-03). |
| Governing ADRs | [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md), [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) |

## Business rule

- The two sets are independent multivalued facts (the 4NF decomposition). Each `PUT` replaces one whole set in a single transaction, serialized per customer by `pg_advisory_xact_lock(290001, hashtext(customer_id::text))`.
- References are checked: the customer through `api_customers.customer_ref_v1` (404 if unknown), channels and categories by FK (23503 maps to 422).

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/preference-profiles/{customer_id}` | Channel ids and category ids of one customer (E-03) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| PUT | `/v1/preference-profiles/{customer_id}/preferred-channels` | Replace the whole set of preferred channels | ADMIN, STORE_MANAGER |
| PUT | `/v1/preference-profiles/{customer_id}/interest-categories` | Replace the whole set of interest categories | ADMIN, STORE_MANAGER |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `customer_preferred_channel` | Table (`public`) | S: SELECT, INSERT, DELETE (whole-set replace) | The web only reads it |
| `customer_interest_category` | Table (`public`) | S: SELECT, INSERT, DELETE (whole-set replace) | The web only reads it |
| `preferences_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_preferences`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_preferences.interest_category_v1` | recommendations |
| Consumes | `api_customers.customer_ref_v1` | customers |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:preferences.v1.changed` | captured from both preference tables | recommendations' cache |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `preferences:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 404 | Undecided; the contract story fixes the code | The customer does not exist in `api_customers.customer_ref_v1` (F14-15 fixes the code) |
| 422 | `unknown_channel` | A channel id references no channel (23503) |
| 422 | `unknown_category` | A category id references no category (23503) |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-03 — a foreign id keys a resource that is its own identity

- **What:** Two paths carry another boundary's id: `/v1/preference-profiles/{customer_id}` and `/v1/stock-levels/{store_id}/{product_id}`. Strictly, "foreign ids are query or body fields".
- **Why it is acceptable:** A preference profile exists once per customer, and a stock level is one row per store and product. The id is the resource's own identity, not a reference to another service's resource, so a query parameter would only hide a key.
- **Guard:** No path may contain another service's registered resource name, as whole path segments. The registry check enforces it. The remaining half, "the id is the resource's identity", is a review call, and only these two paths use it.
- **Decided in:** [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (Decision (path rule)). Register: [E-03](./README.md#e-03--a-foreign-id-keys-a-resource-that-is-its-own-identity).

### E-11 — foreign keys cross service boundaries

- **What:** Tables owned by different services reference each other (`customer.channel_id`, `transaction_line.product_id`, `campaign.label_code` to `segment_label`, `checkins.store_check_in` to `store` and `app_user`).
- **Why it is acceptable:** They come from the legacy schema or protect it. The RI check runs as the parent's owner, so the child's service needs no privilege on the parent. Dropping them would weaken the model the web relies on.
- **Guard:** A violation (23503) maps to a 422 by constraint name. No service holds DELETE on a master, so no cascade starts from a service. The category guard covers the remaining case.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Consequences). Register: [E-11](./README.md#e-11--foreign-keys-cross-service-boundaries).

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
| PostgreSQL unreachable | `/health/ready` reports not ready and operations that need the database fail (the contract fixes the code). `/health/live` stays 200 |
| `redis-state` full | `XADD` of `stream:preferences.v1.changed` fails; the outbox keeps the rows until there is room |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) Compliance (two tables, one service role).
- [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) Compliance (outbox capture, stream ownership).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-15 contract, F14-16 implementation; consumed by F17-04, F16-03. Issue numbers live in [`docs/backlog.md`](../backlog.md).

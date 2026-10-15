# stock

> **Status:** Planned (phase 14, core). ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** Stock is never negative and is consumed only by an accepted sale, atomically with it (RN-14, ADR-0031)

## Summary

| Field | Value |
|---|---|
| Language | Rust (axum) |
| Port (loopback) | 5006 |
| Public path | `/api/stock/v1/` |
| Wave | core |
| PostgreSQL role | `svc_stock` |
| Redis ACL user | `svc_stock`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-stock` / `ghcr.io/equipo-03-proyecto10/mosaiq-stock` |
| Used by | Android: availability at the checked-in store (F17-02). Electron: stock below a threshold (F16-04). |
| Governing ADRs | [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md), [ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md), [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) |

## Business rule

- `quantity_on_hand >= 0` is a CHECK. The only service write is `api_stock.consume_v1`, a SECURITY DEFINER function that runs the web's conditional `UPDATE ... WHERE quantity_on_hand >= q RETURNING` (E-05).
- Availability is a primary-key read and is never cached, so a phone sale and a CSV sale show on the next read. Receipts, transfers and adjustments (`inventory.write`) stay deferred in the roadmap.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/stock-levels` | Stock levels (`store_id`, `product_id`, `below`, `page`, `size`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, INVENTORY_PLANNER, AUDITOR |
| GET | `/v1/stock-levels/{store_id}/{product_id}` | One level (E-03) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, INVENTORY_PLANNER, AUDITOR |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `inventory` | Table (`public`) | R: SELECT. The web's CSV path is a writer (E-09) | `consume_v1` is the only service write |
| `api_stock.consume_v1` | Function | Owned by `definer_stock`: SELECT on `inventory`, UPDATE of `quantity_on_hand` and `updated_at` | EXECUTE to `svc_sales` only |
| `stock_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_stock`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_stock.stock_level_v1` | recommendations |
| Publishes | `api_stock.consume_v1(smallint, int, int)` | sales (EXECUTE only) |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:stock.v1.changed` | captured from `inventory` | recommendations' cache |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `stock:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 422 | `invalid_quantity` | `consume_v1` raised SQLSTATE 22023 (NULL or ≤ 0), seen by sales |
| 409 | `stock_unavailable` | `consume_v1` raised 23514: not enough stock, seen by sales |
| 422 | `no_stock_record` | `consume_v1` raised 23514: no inventory row, seen by sales |
| 404 | Undecided; the contract story fixes the code | An unknown store and product pair on the read endpoint (F14-11 fixes the code) |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-03 — a foreign id keys a resource that is its own identity

- **What:** Two paths carry another boundary's id: `/v1/preference-profiles/{customer_id}` and `/v1/stock-levels/{store_id}/{product_id}`. Strictly, "foreign ids are query or body fields".
- **Why it is acceptable:** A preference profile exists once per customer, and a stock level is one row per store and product. The id is the resource's own identity, not a reference to another service's resource, so a query parameter would only hide a key.
- **Guard:** No path may contain another service's registered resource name, as whole path segments. The registry check enforces it. The remaining half, "the id is the resource's identity", is a review call, and only these two paths use it.
- **Decided in:** [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (Decision (path rule)). Register: [E-03](./README.md#e-03--a-foreign-id-keys-a-resource-that-is-its-own-identity).

### E-05 — one synchronous cross-boundary call, inside the database

- **What:** sales-service calls `api_stock.consume_v1` once per sale line, inside its own transaction. It is the only place where one boundary calls into another.
- **Why it is acceptable:** A sale and its stock decrement must commit together (RN-14, ADR-0031). A saga or an HTTP call to stock could leave a sale without its decrement, or the reverse. A SECURITY DEFINER function owned by `definer_stock` gives atomicity without a stock write privilege for sales.
- **Guard:** EXECUTE goes to `svc_sales` only, and the grant check compares it both ways. It is in the database and never HTTP. ADR-0044 states that a second cross-boundary write needs its own record.
- **Decided in:** [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (The one synchronous call); [ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md) (The transaction). Register: [E-05](./README.md#e-05--one-synchronous-cross-boundary-call-inside-the-database).

### E-08 — read-rule services count as one-rule services

- **What:** Four services write no invariant of their own. They enforce a read rule: what they may report, and to whom.
- **Why it is acceptable:** Counting them follows the instructor's wording (one business rule per service), and the domain does not offer more writers: segmentation runs belong to the web (RN-05), and the audit log is written by `fn_audit`.
- **Guard:** The instructor confirmed that read-only boundaries count (Q-13, 2026-10-15). Even under the strict reading the floor would hold: ten core writers plus campaigns. ADR-0043 forbids cutting any core service.
- **Decided in:** [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (What counts, and the margin if Q-13 goes against it). Register: [E-08](./README.md#e-08--read-rule-services-count-as-one-rule-services).

### E-09 — the frozen web is a legacy co-writer of some tables

- **What:** The web still writes `app_user`, `product`, `category`, `campaign`, `transaction`, `transaction_line` and, through its CSV path, `inventory`. "Only service writer" is not "only writer".
- **Why it is acceptable:** The web is frozen, yet a phone sale must appear in it and its edits must keep working. Services therefore write the same `public` tables the web reads.
- **Guard:** Rules both enforce live in the schema (CHECKs, unique indexes, triggers) or are pinned by golden fixtures (E-17). The owner's outbox trigger captures web writes too, so no writer is invisible.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Context); [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (Events). Register: [E-09](./README.md#e-09--the-frozen-web-is-a-legacy-co-writer-of-some-tables).

### E-10 — read owners of tables only the web writes

- **What:** Some services read a table they never write (`role`, `store`, `inventory`, `channel`, the segmentation tables, `audit_log`) and publish it through a contract.
- **Why it is acceptable:** Without a declared owner, several services would read `store` directly and nobody would know who a change breaks. One read owner gives one service to change and one contract for everyone else.
- **Guard:** Exactly one `svc_*` role holds any privilege on each base table, and CI checks it. A read owner cannot fix its own data: the web does.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Decision and ownership matrix). Register: [E-10](./README.md#e-10--read-owners-of-tables-only-the-web-writes).

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
| PostgreSQL unreachable | `/health/ready` reports not ready, and every operation that reads or writes PostgreSQL answers 503 `database_unavailable`, never 500 (ADR-0035). `/health/live` stays 200 |
| `redis-state` full | `XADD` of `stream:stock.v1.changed` fails; the outbox keeps the rows until there is room. Reads are unaffected |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) Compliance (`consume_v1` executable by `svc_sales` only; definer ownership; outbox capture).
- [ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md) Compliance (a short multi-line sale leaves nothing behind).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-11 contract, F14-12 implementation; consumed by F17-02, F16-04. Issue numbers live in [`docs/backlog.md`](../backlog.md).

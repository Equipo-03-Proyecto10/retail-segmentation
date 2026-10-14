# products

> **Status:** Planned (phase 14, core). Not built until ADR-0034 to ADR-0046 are accepted (F13-01, #408).
> **One rule:** A product has a unique SKU, a price that is never negative and exactly one existing category, and is retired by deactivation (RN-10, RN-11)

## Summary

| Field | Value |
|---|---|
| Language | Flask |
| Port (loopback) | 5003 |
| Public path | `/api/products/v1/` |
| Wave | core |
| PostgreSQL role | `svc_products` |
| Redis ACL user | `svc_products`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-products` / `ghcr.io/equipo-03-proyecto10/mosaiq-products` |
| Used by | Android: QR detail and one batch lookup per recommendation list (F17-02, F17-05). Electron: product maintenance and product names for sales read-back (F16-02, F16-04). |
| Governing ADRs | [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md), [ADR-0042](../adr/0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md) |

## Business rule

- SKU is unique (409 `sku_taken`), `list_price >= 0` (CHECK) and `category_id` is an existing category (FK; 23503 maps to 422 `unknown_category`).
- A product is retired by `is_active = false`, never deleted: no DELETE grant. `image_path` stays web-managed and is read-only here.
- `GET /v1/products?ids=` is a batch read of at most 50 ids, so clients avoid one lookup per product.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/products` | List (`q`, `sku`, `category_id`, `active`, `ids` up to 50, `page`, `size`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, INVENTORY_PLANNER, AUDITOR |
| GET | `/v1/products/{product_id}` | One product (the QR payload is `product_id`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, INVENTORY_PLANNER, AUDITOR |
| POST | `/v1/products` | Create | ADMIN |
| PATCH | `/v1/products/{product_id}` | Edit or deactivate | ADMIN |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `product` | Table (`public`) | S+W: SELECT, INSERT, UPDATE of `name`, `category_id`, `list_price`, `is_active`; no DELETE | `image_path` is web-managed; the web is a legacy co-writer (E-09) |
| `products_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_products`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_products.product_ref_v1` | sales, recommendations, engagement |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:products.v1.changed` | captured from `product` | Own cache and recommendations' cache |
| Consumes | `stream:products.v1.changed` | group `products` | Invalidates its own cache, including web edits and hard deletes |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| cache | `products:cache:*` | 10 min | Product reads, invalidated on change |
| state | `products:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 409 | `sku_taken` | The SKU already exists |
| 422 | `unknown_category` | `category_id` references no category (23503) |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-09 — the frozen web is a legacy co-writer of some tables

- **What:** The web still writes `app_user`, `product`, `category`, `campaign`, `transaction`, `transaction_line` and, through its CSV path, `inventory`. "Only service writer" is not "only writer".
- **Why it is acceptable:** The web is frozen, yet a phone sale must appear in it and its edits must keep working. Services therefore write the same `public` tables the web reads.
- **Guard:** Rules both enforce live in the schema (CHECKs, unique indexes, triggers) or are pinned by golden fixtures (E-17). The owner's outbox trigger captures web writes too, so no writer is invisible.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Context); [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (Events). Register: [E-09](./README.md#e-09--the-frozen-web-is-a-legacy-co-writer-of-some-tables).

### E-12 — audit_log is the one table every writer shares

- **What:** Every audited write, from any service or the web, lands in `audit_log` through `fn_audit`. audit-trail reads it.
- **Why it is acceptable:** The audit is a cross-cutting append-only record (RN-28 to RN-30). One writer function keeps it uniform and attributable, and services pass the actor with `set_config('mosaiq.user_id', <sub>, true)`.
- **Guard:** `fn_audit` is SECURITY DEFINER, owned by `definer_audit`. No `svc_*` role writes the table, and only `svc_audit_trail` reads it.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Compliance); [ADR-0046](../adr/0046-service-owned-data-lives-in-service-schemas.md) (The tables). Register: [E-12](./README.md#e-12--audit_log-is-the-one-table-every-writer-shares).

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

### E-22 — a bench-only Rust build serves the same read endpoints as products

- **What:** `bench/products-service-rs/` implements the products read operations so the two languages can be compared.
- **Why it is acceptable:** A fair comparison needs the same contract under the same load. Two services doing different work cannot be compared.
- **Guard:** It is never registered, routed, deployed or counted, so no endpoint is served by two deployables. It connects as `svc_products_bench` with SELECT on `public.product` only.
- **Decided in:** [ADR-0042](../adr/0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md) (Decision). Register: [E-22](./README.md#e-22--a-bench-only-rust-build-serves-the-same-read-endpoints-as-products).

## Failure behaviour

| Dependency down | Effect |
|---|---|
| PostgreSQL unreachable | `/health/ready` reports not ready and operations that need the database fail (the contract fixes the code). `/health/live` stays 200 |
| `redis-cache` down | Reads fall through to PostgreSQL; same answer, slower |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| `redis-state` full or down | The relay cannot `XADD`; unrelayed outbox rows wait until there is room, and consumers' caches expire by TTL |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) Compliance (no DELETE on `product`).
- [ADR-0042](../adr/0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md) Compliance (the bench build passes the same contract suite).
- Conformance suite: JSON and XSD-valid XML, 406 for any other `Accept`.
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-05 contract, F14-06 implementation; consumed by F17-02, F17-05, F16-02, F16-04. Bench twin: F15-01, F15-02. Issue numbers live in [`docs/backlog.md`](../backlog.md).

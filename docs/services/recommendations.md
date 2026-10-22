# recommendations

> **Status:** Implemented (phase 14, core; F14-22, #477). ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** Recommend active, in-stock products not already bought, matching a stated signal, with reasons, or report why nothing qualifies (RN-40)

## Summary

| Field | Value |
|---|---|
| Language | Flask |
| Port (loopback) | 5011 |
| Public path | `/api/recommendations/v1/` |
| Wave | core |
| PostgreSQL role | `svc_recommendations` (no table of its own) |
| Redis ACL user | `svc_recommendations`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-recommendations` / `ghcr.io/equipo-03-proyecto10/mosaiq-recommendations` |
| Used by | Android: recommendations at the counter, one `RECO_CLICK` per tap (F17-05). Electron: recommendations review (F16-03). |
| Governing ADRs | [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) |

## Business rule

- A product is recommended only when it is active, in stock at the customer's usual store, not already bought in the window, and matches at least one signal: segment peers, an interest category or a purchase-history category, matched over the category subtree. Each item carries exactly `product_id`, `category_id` (the product's own category), `quantity_on_hand` (the stock held at the usual store, E-06) and `reasons`. Reasons come in the fixed order `segment`, `preferred_category`, `purchase_history`, each with its signal and figures: `segment_buyers`; the nearest matching category as `category_id`; that `category_id` and its `share`. A segment-only item has no category reason.
- When nothing qualifies, the response says why: `no_open_assignment`, `unassigned_result` or `no_usual_store`. Items carry ids; clients fetch product names with `GET /v1/products?ids=`.
- Owns no table. Its result cache lasts 120 s, and parity with the web's rule is proven by golden fixtures for all seeded customers (E-17). Read-rule service (E-08).

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/recommendations?customer_id=&limit=` | Recommendations with reasons, or why none (`limit` 1 to 50, default 10) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| PostgreSQL | — | No table | SELECT on seven published views only; a result cache in `redis-cache` |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Consumes | `api_products.product_ref_v1` | products |
| Consumes | `api_categories.category_tree_v1` | categories |
| Consumes | `api_stock.stock_level_v1` | stock |
| Consumes | `api_customers.customer_ref_v1` | customers |
| Consumes | `api_preferences.interest_category_v1` | customer-preferences |
| Consumes | `api_sales.purchase_line_v1` | sales |
| Consumes | `api_segments.current_label_v1` | segments |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Consumes | `stream:products.v1.changed` | group `recommendations` | Invalidates its result cache |
| Consumes | `stream:categories.v1.changed` | group `recommendations` | Invalidates its result cache |
| Consumes | `stream:stock.v1.changed` | group `recommendations` | Invalidates its result cache |
| Consumes | `stream:preferences.v1.changed` | group `recommendations` | Invalidates its result cache |
| Consumes | `stream:sales.v1.recorded` | group `recommendations` | Invalidates its result cache |
| Consumes | `stream:segments.v1.run_recorded` | group `recommendations` | Invalidates its result cache |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| cache | `recommendations:cache:{customer_id}:{run_id}:{limit}` | 120 s | Result cache; the staleness bound is stated in the contract |
| state | `recommendations:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 422 | `unknown_customer` | `customer_id` is absent from `api_customers.customer_ref_v1` |
| 422 | `invalid_query` | `customer_id` missing or malformed, or `limit` outside 1 to 50; `details` names the parameter |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-06 — a recommendation carries the stock figure it used

- **What:** Each recommended item includes the quantity held, which belongs to stock.
- **Why it is acceptable:** The figure is part of the evidence for the service's own decision ("in stock at the customer's usual store"), so it is part of this service's representation, not a re-serving of stock. Dropping it would hide why an item was or was not offered.
- **Guard:** Only a figure the service used to decide is allowed, never another service's representation. Product names, prices and availability queries stay with products and stock, and clients fetch them by id.
- **Decided in:** [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (Decision (no composition)). Register: [E-06](./README.md#e-06--a-recommendation-carries-the-stock-figure-it-used).

### E-07 — recommendations depends on seven published contracts

- **What:** One service reads seven boundaries: products, categories, stock, customers, preferences, sales and segments.
- **Why it is acceptable:** RN-40 is a rule over all of them. Splitting it would put one rule in several services, or copy seven datasets into projections that drift.
- **Guard:** It reads versioned `api_*` views only, never a base table, and names the columns it reads. A breaking change ships `_v2` beside `_v1`, and the old object is dropped only when `grants.csv` shows no consumer.
- **Decided in:** [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (Reads: the published database contracts). Register: [E-07](./README.md#e-07--recommendations-depends-on-seven-published-contracts).

### E-08 — read-rule services count as one-rule services

- **What:** Four services write no invariant of their own. They enforce a read rule: what they may report, and to whom.
- **Why it is acceptable:** Counting them follows the instructor's wording (one business rule per service), and the domain does not offer more writers: segmentation runs belong to the web (RN-05), and the audit log is written by `fn_audit`.
- **Guard:** The instructor confirmed that read-only boundaries count (Q-13, 2026-10-15). Even under the strict reading the floor would hold: ten core writers plus campaigns. ADR-0043 forbids cutting any core service.
- **Decided in:** [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (What counts, and the margin if Q-13 goes against it). Register: [E-08](./README.md#e-08--read-rule-services-count-as-one-rule-services).

### E-17 — rules ported from the web exist twice

- **What:** Four rules exist in the web and in a service: sale validation, the RN-40 recommendation rule, user outcomes and campaign transitions.
- **Why it is acceptable:** A service may not import `web/`, which is frozen, loads Flask and has no version to pin. Carrying a rule twice is the price of independence.
- **Guard:** The database carries what it can (CHECKs, indexes, triggers). The rest is a port pinned by golden fixtures captured once from the web on the seed database, with a drift job. A web bug fix opens an issue to regenerate them.
- **Decided in:** [ADR-0045](../adr/0045-services-never-import-the-web-system.md) (Decision); [ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md) (Context). Register: [E-17](./README.md#e-17--rules-ported-from-the-web-exist-twice).

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
| `redis-cache` down | Results are recomputed from the published views; same answer, slower |
| A producer service down | No effect on answers: the views are read from PostgreSQL. Events wait in the streams, and the cache expires by TTL |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) Compliance (seven consumed views, six consumer groups).
- [ADR-0045](../adr/0045-services-never-import-the-web-system.md) Compliance (golden fixtures for RN-40 on every seeded customer).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-21 contract, F14-22 implementation; consumed by F17-05, F16-03. Issue numbers live in [`docs/backlog.md`](../backlog.md).

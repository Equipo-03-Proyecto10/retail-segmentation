# recommendations

> **Status:** `/v1` implemented (phase 14, core; F14-22, #477). The `/v2` contract is published (F24-03, #826, ADR-0067); its implementation is F24-04 to F24-07, so the rows and sections below that describe it are marked *planned*. ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** Recommend only products that are available at the reference store and channel (the one the caller names, else the customer's usual store), not already bought and matching a stated signal, with reasons, marking any alternative and recording and reading back the explanation of each decision, or report why nothing qualifies (RN-40, ADR-0067)

## Summary

| Field | Value |
|---|---|
| Language | Flask |
| Port (loopback) | 5011 |
| Public path | `/api/recommendations/v1/` |
| Wave | core |
| PostgreSQL role | `svc_recommendations`. `/v1` needs no table; the decision record is a schema of its own (ADR-0046, ADR-0067), created by F24-05 |
| Redis ACL user | `svc_recommendations`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-recommendations` / `ghcr.io/equipo-03-proyecto10/mosaiq-recommendations` |
| Used by | Android: recommendations at the counter, one `RECO_CLICK` per tap (F17-05); the store-aware list with marked alternatives and impressions moves to `/v2` (F27-06). Electron: recommendations review (F16-03); the inspection module reads `/v2` and the decisions (F28-08). |
| Governing ADRs | [ADR-0067](../adr/0067-a-recommendation-is-judged-at-a-reference-store-and-channel-and-its-explanation-is-recorded.md), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) |

## Business rule

- A product is recommended only when it is active, in stock at the customer's usual store, not already bought in the window, and matches at least one signal: segment peers, an interest category or a purchase-history category, matched over the category subtree. Each item carries exactly `product_id`, `category_id` (the product's own category), `quantity_on_hand` (the stock held at the usual store, E-06) and `reasons`. Reasons come in the fixed order `segment`, `preferred_category`, `purchase_history`, each with its signal and figures: `segment_buyers`; the nearest matching category as `category_id`; that `category_id` and its `share`. A segment-only item has no category reason.
- When nothing qualifies, the response says why: `no_open_assignment`, `unassigned_result` or `no_usual_store`. Items carry ids; clients fetch product names with `GET /v1/products?ids=`.
- `/v1` owns no table. Its result cache lasts 120 s, and parity with the web's rule is proven by golden fixtures for the default path of all seeded customers (E-17).

### Since ADR-0067 (`/v2`, contract published in F24-03, implementation planned in F24-04 to F24-07)

- **Reference store and channel.** `GET /v2/recommendations` takes an optional `store_id` and `channel_id`. The reference store is the one named, else the customer's usual store (`store_source` says which); the channel is never defaulted. A floor role naming a store it is not assigned to gets 403 `forbidden_store` (ADR-0060); with no `store_id` it is not narrowed. A customer with no usual store and no `store_id` gets `no_usual_store`.
- **Available** means: the commercial status allows the sale, the store's status permits sales, the store enables the channel when one is given, and on-hand is above zero. Opening hours are not part of it. A product that matches a signal and fails is excluded for exactly one reason, in this order: `already_bought`, `inactive`, `not_sellable`, `store_not_selling`, `channel_not_enabled`, `out_of_stock`, `PERSONALIZATION_WITHDRAWN`. The response counts them (`exclusions`) and never lists them.
- **Each item** has a `kind` (`primary`, `alternative`, `elsewhere`), a `position`, its `factors` (the figures RN-40 orders by), its `availability` (store, channel and the quantity used, E-06) and its `reasons`. An `alternative` is one product related to an unavailable one by a declared substitute relation and itself available; it names the product it replaces (`alternative_for`) and why that was unavailable. An `elsewhere` entry reports another store holding the product and is never a recommendation for the reference store.
- **The explanation is recorded.** Every answer carries a `decision_id`. The decision (ids, figures and codes only, never a name, email, phone, SKU, price or free text) is kept for 90 days, subject to F21-15, and is read back with `GET /v2/recommendation-decisions/{decision_id}`.
- **Consent.** A customer who objected to personalization gets `PERSONALIZATION_WITHDRAWN` with no items, and no segment, history or interest is read for them.
- **Default path.** With no store, no channel and every product active and sellable, the answer is what the web returns, which the golden fixtures keep pinning; every other behaviour above is outside the parity claim (E-17).

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/recommendations?customer_id=&limit=` | Recommendations with reasons, or why none (`limit` 1 to 50, default 10). Frozen: judged at the usual store | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| GET | `/v2/recommendations?customer_id=&limit=&store_id=&channel_id=` | *Planned.* Judged at a reference store and channel, with factors, availability, marked alternatives and a `decision_id` | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR, SALES_ASSOCIATE, LOYALTY_OFFICER (ADR-0060) |
| GET | `/v2/recommendation-decisions?customer_id=&store_id=&status=&from=&to=&page=&size=` | *Planned.* Recorded decisions as summaries | ADMIN, ANALYST, AUDITOR, STORE_MANAGER (narrowed to its assigned stores) |
| GET | `/v2/recommendation-decisions/{decision_id}` | *Planned.* One recorded decision: what was offered, what was excluded and why | ADMIN, ANALYST, AUDITOR, STORE_MANAGER (narrowed to its assigned stores) |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`, `GET /schema.v2.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| PostgreSQL | — | `/v1`: no table | SELECT on seven published views only; a result cache in `redis-cache` |
| PostgreSQL, schema `recommendations` | tables | S (*planned*, F24-05) | The decision record (ADR-0067, ADR-0046): per decision the customer id, the reference store and channel, the status, the exclusion counts, the segmentation run and model version and the computation time; per item its product, kind, position, quantity, `alternative_for` and one row per reason. Ids, figures and codes only. Retained 90 days; anonymization clears the customer id. The tables, their 4NF justification, grants and seed arrive with F24-05 |

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
| Consumes (*planned*, `/v2`) | `api_stock.availability_v1` | stock |
| Consumes (*planned*, `/v2`) | The commercial-status and substitute-relation views of products (F21-09, F21-10) and the status and channel views of stores (F21-11, F21-12); names fixed by those stories | products, stores |
| Consumes (*planned*, `/v2`) | The restriction view of customers (F21-15, F21-17) | customers |

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
| cache | `recommendations:cache:{customer_id}:{run_id}:{limit}` | 120 s | `/v1` result cache; the staleness bound is stated in the contract. For `/v2` the key also carries the store and the channel (ADR-0067), or a seller's result would be served to another store |
| state | `recommendations:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 422 | `unknown_customer` | `customer_id` is absent from `api_customers.customer_ref_v1` |
| 422 | `invalid_query` | `customer_id` missing or malformed, or `limit` outside 1 to 50; on `/v2` also `store_id` or `channel_id` not a positive integer; on the decision list a malformed filter; `details` names the parameter |
| 422 | `unknown_store` | `/v2`: `store_id` names a store that does not exist, for a role that is not a floor role |
| 422 | `unknown_channel` | `/v2`: `channel_id` names a channel that does not exist |
| 403 | `forbidden_store` | `/v2`: a floor role names a `store_id` it is not assigned to, or a `STORE_MANAGER` reads a decision made at another store (ADR-0060) |
| 404 | `decision_not_found` | `/v2`: no decision with that id is held, or its retention has passed |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-06 — a recommendation carries the stock figure it used

- **What:** Each recommended item includes the quantity held, which belongs to stock. On `/v2` the item's `availability` also names the store and channel the figure belongs to, and an `elsewhere` entry carries another store's quantity.
- **Why it is acceptable:** The figure is part of the evidence for the service's own decision ("available at the reference store and channel"), so it is part of this service's representation, not a re-serving of stock. Dropping it would hide why an item was or was not offered.
- **Guard:** Only a figure the service used to decide is allowed, never another service's representation: `availability` is not stock's `availabilities` resource and carries no `reason` or `updated_at`. Product names, prices and availability queries stay with products and stock, and clients fetch them by id.
- **Decided in:** [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (Decision (no composition)). Register: [E-06](./README.md#e-06--a-recommendation-carries-the-stock-figure-it-used).

### E-07 — recommendations depends on seven published contracts

- **What:** One service reads seven boundaries: products, categories, stock, customers, preferences, sales and segments. For `/v2` it also reads stock's `availability_v1`, the products and stores views of commercial status, substitute relations, store status and channels, and the customer restriction view (ADR-0067).
- **Why it is acceptable:** RN-40 is a rule over all of them. Splitting it would put one rule in several services, or copy seven datasets into projections that drift. The number is a consequence of the rule, not a reason to split it.
- **Guard:** It reads versioned `api_*` views only, never a base table, and names the columns it reads. A breaking change ships `_v2` beside `_v1`, and the old object is dropped only when `grants.csv` shows no consumer.
- **Decided in:** [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (Reads: the published database contracts). Register: [E-07](./README.md#e-07--recommendations-depends-on-seven-published-contracts).

### E-08 — read-rule services count as one-rule services

- **What:** Four services write no invariant of their own. They enforce a read rule: what they may report, and to whom. Since ADR-0067 recommendations also records the explanation of each decision, in a schema of its own (F24-05). That record is its own output and not an invariant other services depend on, so it is still counted as a read-rule service.
- **Why it is acceptable:** Counting them follows the instructor's wording (one business rule per service), and the domain does not offer more writers: segmentation runs belong to the web (RN-05), and the audit log is written by `fn_audit`.
- **Guard:** The instructor confirmed that read-only boundaries count (Q-13, 2026-10-15). Even under the strict reading the floor would hold: ten core writers plus campaigns. ADR-0043 forbids cutting any core service.
- **Decided in:** [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (What counts, and the margin if Q-13 goes against it). Register: [E-08](./README.md#e-08--read-rule-services-count-as-one-rule-services).

### E-17 — rules ported from the web exist twice

- **What:** Four rules exist in the web and in a service: sale validation, the RN-40 recommendation rule, user outcomes and campaign transitions. For RN-40, parity holds on the default path only (ADR-0067): no store and no channel named, every product active and sellable. A given store or channel, `not_sellable`, `store_not_selling`, alternatives, the recorded explanation and consent are not in the web and are outside the parity claim.
- **Why it is acceptable:** A service may not import `web/`, which is frozen, loads Flask and has no version to pin. Carrying a rule twice is the price of independence. The web cannot read the stock-channel, commercial-status and consent contracts, so it does not adopt the new behaviours.
- **Guard:** The database carries what it can (CHECKs, indexes, triggers). The rest is a port pinned by golden fixtures captured once from the web on the seed database, with a drift job. A web bug fix opens an issue to regenerate them. Each deliberate difference of RN-40 is a recorded fixture with its reason (ADR-0045).
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

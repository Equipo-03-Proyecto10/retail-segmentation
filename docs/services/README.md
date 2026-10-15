# Services

One sheet per microservice, all with the same structure. A sheet describes the decided design of one service in one place: its rule, endpoints, data, contracts, events, Redis keys, errors, accepted exceptions and failure behaviour. **Sheets are not code and never override an ADR.** When a sheet and an ADR disagree, the ADR wins and the sheet is wrong.

The design is governed by ADR-0034 to ADR-0046. Every service implements exactly one business rule ([ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md)), owns its resources exclusively ([ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)) and meets the others only through versioned database contracts and Redis Streams ([ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)). Nothing here is built until those records are accepted (F13-01, #408).

## Index

| Service | Language | Port | Wave | One rule (short) | RN ids |
|---|---|---|---|---|---|
| [auth](./auth.md) | Flask | 5001 | core | Authenticates by a valid credential of an active account; a token lives only while its session is live | RN-52; related RN-03 |
| [users](./users.md) | Flask | 5002 | core | One administrator, never two or zero; unique emails; accounts deactivated, never deleted | RN-01, RN-02, RN-04 |
| [products](./products.md) | Flask | 5003 | core | Unique SKU, non-negative price, one existing category; retired, not deleted | RN-10, RN-11 |
| [categories](./categories.md) | Flask | 5004 | core | An acyclic category tree; no delete with products, children or customer interests | RN-33, RN-06, RN-07 |
| [stores](./stores.md) | Rust (axum) | 5005 | core | At most one valid, geofenced location per store, set by ADMIN only | RN-53 |
| [stock](./stock.md) | Rust (axum) | 5006 | core | Stock never negative, consumed only by an accepted sale, atomically with it | RN-14, ADR-0031 |
| [customers](./customers.md) | Flask | 5007 | core | A customer registered once with valid data, never deleted through services | RN-08 |
| [customer-preferences](./customer-preferences.md) | Rust (axum) | 5008 | core | Preferred channels and interest categories each replaced only as a whole set | RN-54 |
| [sales](./sales.md) | Flask | 5009 | core | A sale recorded once, all or nothing, consuming its stock in the same transaction | RN-11 to RN-13, RN-37 (accepted sales) |
| [segments](./segments.md) | Rust (axum) | 5010 | core | At most one current segment per customer, by stable label; unassigned reported as is | RN-20, RN-21, ADR-0018 |
| [recommendations](./recommendations.md) | Flask | 5011 | core | Recommend active, in-stock, not-yet-bought products with reasons, or say why none | RN-40 |
| [engagement](./engagement.md) | Flask + MongoDB | 5012 | core | QR scans and recommendation clicks recorded once, attributed, voided never deleted | RN-55 |
| [check-ins](./check-ins.md) | Rust (axum) | 5013 | core | A check-in is verified only within the store's geofence; overrides are append-only reviews | RN-56 |
| [campaigns](./campaigns.md) | Flask | 5014 | second | A campaign moves only along the allowed states, is edited only in DRAFT | RN-15, RN-16, RN-22, RN-24 (activation), RN-31, RN-32, RN-49 (activation) |
| [audit-trail](./audit-trail.md) | Rust (axum) | 5015 | second | An append-only, attributed, credential-free audit readable by ADMIN and AUDITOR only | RN-28, RN-29, RN-30 |

The full one-sentence rule of each service is in its sheet and in the registry of [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md).

## How to read a sheet

- **Summary:** language, port, role, clients that use it and governing ADRs.
- **Business rule:** what is enforced, and where.
- **Endpoints:** every business endpoint with its roles. Meta routes are common to all services.
- **Data it owns:** tables, collections and Redis state, with S/W/R access.
- **Database contracts:** the `api_*` objects it publishes and consumes.
- **Events:** the Redis Streams it produces and consumes.
- **Redis keys:** its prefixes, instance and TTL.
- **Service-specific errors:** its own codes; the shared ones are in ADR-0035.
- **Accepted exceptions:** where it deviates from the strict reading of "one rule", "never mix endpoints" or "total independence", each with a justification and a guard.
- **Failure behaviour:** what happens when a dependency is down.
- **Compliance:** the checks that prove the sheet.
- **Backlog:** story ids; GitHub issue numbers are in `docs/backlog.md`.

## Accepted exceptions register

The design accepts a small number of deliberate deviations from the strict reading of the instructor's requirements: **one business rule per service, never mix endpoints, total independence**. Each is listed here once, with the reason it is acceptable and the guard that keeps it from growing. Each service sheet repeats the ones that concern it. The decision itself lives in the ADR named under *Decided in*.

| Id | Exception | Services |
|---|---|---|
| [E-01](#e-01--sales-serves-the-read-only-channel-vocabulary) | sales serves the read-only channel vocabulary | [sales](./sales.md) |
| [E-02](#e-02--users-serves-the-read-only-role-vocabulary) | users serves the read-only role vocabulary | [users](./users.md) |
| [E-03](#e-03--a-foreign-id-keys-a-resource-that-is-its-own-identity) | a foreign id keys a resource that is its own identity | [customer-preferences](./customer-preferences.md), [stock](./stock.md) |
| [E-04](#e-04--the-public-key-is-served-as-json-only) | the public key is served as JSON only | [auth](./auth.md) |
| [E-05](#e-05--one-synchronous-cross-boundary-call-inside-the-database) | one synchronous cross-boundary call, inside the database | [stock](./stock.md), [sales](./sales.md) |
| [E-06](#e-06--a-recommendation-carries-the-stock-figure-it-used) | a recommendation carries the stock figure it used | [recommendations](./recommendations.md) |
| [E-07](#e-07--recommendations-depends-on-seven-published-contracts) | recommendations depends on seven published contracts | [recommendations](./recommendations.md) |
| [E-08](#e-08--read-rule-services-count-as-one-rule-services) | read-rule services count as one-rule services | [stock](./stock.md), [segments](./segments.md), [recommendations](./recommendations.md), [audit-trail](./audit-trail.md) |
| [E-09](#e-09--the-frozen-web-is-a-legacy-co-writer-of-some-tables) | the frozen web is a legacy co-writer of some tables | [users](./users.md), [products](./products.md), [categories](./categories.md), [campaigns](./campaigns.md), [sales](./sales.md), [stock](./stock.md) |
| [E-10](#e-10--read-owners-of-tables-only-the-web-writes) | read owners of tables only the web writes | [users](./users.md), [stores](./stores.md), [stock](./stock.md), [sales](./sales.md), [segments](./segments.md), [audit-trail](./audit-trail.md) |
| [E-11](#e-11--foreign-keys-cross-service-boundaries) | foreign keys cross service boundaries | [customers](./customers.md), [customer-preferences](./customer-preferences.md), [sales](./sales.md), [check-ins](./check-ins.md), [stores](./stores.md), [campaigns](./campaigns.md) |
| [E-12](#e-12--audit_log-is-the-one-table-every-writer-shares) | audit_log is the one table every writer shares | [audit-trail](./audit-trail.md), [users](./users.md), [products](./products.md), [categories](./categories.md), [stores](./stores.md), [customers](./customers.md), [campaigns](./campaigns.md) |
| [E-13](#e-13--a-definer-guard-reads-rows-its-service-cannot-read) | a definer guard reads rows its service cannot read | [categories](./categories.md), [campaigns](./campaigns.md) |
| [E-14](#e-14--campaigns-shares-the-webs-id-allocation-lock) | campaigns shares the web's id-allocation lock | [campaigns](./campaigns.md) |
| [E-15](#e-15--the-credential-view-carries-the-password-hash) | the credential view carries the password hash | [auth](./auth.md), [users](./users.md) |
| [E-16](#e-16--phone-sales-share-the-transaction-id-namespace-with-csv) | phone sales share the transaction id namespace with CSV | [sales](./sales.md) |
| [E-17](#e-17--rules-ported-from-the-web-exist-twice) | rules ported from the web exist twice | [sales](./sales.md), [recommendations](./recommendations.md), [users](./users.md), [campaigns](./campaigns.md) |
| [E-18](#e-18--never-deleted-is-enforced-in-service-code-not-in-mongodb) | "never deleted" is enforced in service code, not in MongoDB | [engagement](./engagement.md) |
| [E-19](#e-19--one-postgresql-one-ddl-file-one-redis-state-one-mongodb) | one PostgreSQL, one DDL file, one redis-state, one MongoDB | all 15 |
| [E-20](#e-20--the-liveness-key-and-the-jwks-file-are-read-by-every-service) | the liveness key and the JWKS file are read by every service | all 15 |
| [E-21](#e-21--a-mechanism-only-kit-is-shared-by-every-service) | a mechanism-only kit is shared by every service | all 15 |
| [E-22](#e-22--a-bench-only-rust-build-serves-the-same-read-endpoints-as-products) | a bench-only Rust build serves the same read endpoints as products | [products](./products.md) |

### E-01 — sales serves the read-only channel vocabulary

- **What:** sales-service serves `GET /v1/channels`, the list of sales channels. Strictly, a channel is not a sale.
- **Why it is acceptable:** The sales rule validates every sale's `channel_id` against this vocabulary, so sales already reads it, and the Android client must offer it when it records a sale. Only the web writes `channel` (five rows). A separate service for five read-only rows would be a filler service, which the instructor excluded.
- **Guard:** The operation is GET only and registered to sales alone, so no other service may serve `channels`. ADR-0043 limits the exception to a vocabulary the serving service's own rule validates against. A write endpoint would make it a second rule.
- **Services:** [sales](./sales.md)
- **Decided in:** [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (Decision); [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Registry)

### E-02 — users serves the read-only role vocabulary

- **What:** users-service serves `GET /v1/roles` and `GET /v1/roles/{role_code}`, the seven role codes.
- **Why it is acceptable:** The single-administrator rule is defined over the `ADMIN` role, and every account carries a role, so users already validates role codes. Only the web writes `role`, whose codes are immutable (`trg_role_code_immutable`).
- **Guard:** GET only, registered to users alone, and bound by the same ADR-0043 limit as E-01. Role codes are never created or edited through services.
- **Services:** [users](./users.md)
- **Decided in:** [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (Decision); [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Registry)

### E-03 — a foreign id keys a resource that is its own identity

- **What:** Two paths carry another boundary's id: `/v1/preference-profiles/{customer_id}` and `/v1/stock-levels/{store_id}/{product_id}`. Strictly, "foreign ids are query or body fields".
- **Why it is acceptable:** A preference profile exists once per customer, and a stock level is one row per store and product. The id is the resource's own identity, not a reference to another service's resource, so a query parameter would only hide a key.
- **Guard:** No path may contain another service's registered resource name, as whole path segments. The registry check enforces it. The remaining half, "the id is the resource's identity", is a review call, and only these two paths use it.
- **Services:** [customer-preferences](./customer-preferences.md), [stock](./stock.md)
- **Decided in:** [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (Decision (path rule))

### E-04 — the public key is served as JSON only

- **What:** `GET /v1/jwks.json` answers only `application/jwk-set+json`. Every other operation answers JSON and XML.
- **Why it is acceptable:** RFC 7517 defines no XML form of a JWK Set. No client calls it and services read the mounted file, so it serves verifiers outside the stack.
- **Guard:** It is the single JSON-only operation. It needs no token, and an `Accept` that excludes JSON gets 406.
- **Services:** [auth](./auth.md)
- **Decided in:** [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (Formats); [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md) (Keys)

### E-05 — one synchronous cross-boundary call, inside the database

- **What:** sales-service calls `api_stock.consume_v1` once per sale line, inside its own transaction. It is the only place where one boundary calls into another.
- **Why it is acceptable:** A sale and its stock decrement must commit together (RN-14, ADR-0031). A saga or an HTTP call to stock could leave a sale without its decrement, or the reverse. A SECURITY DEFINER function owned by `definer_stock` gives atomicity without a stock write privilege for sales.
- **Guard:** EXECUTE goes to `svc_sales` only, and the grant check compares it both ways. It is in the database and never HTTP. ADR-0044 states that a second cross-boundary write needs its own record.
- **Services:** [stock](./stock.md), [sales](./sales.md)
- **Decided in:** [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (The one synchronous call); [ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md) (The transaction)

### E-06 — a recommendation carries the stock figure it used

- **What:** Each recommended item includes the quantity held, which belongs to stock.
- **Why it is acceptable:** The figure is part of the evidence for the service's own decision ("in stock at the customer's usual store"), so it is part of this service's representation, not a re-serving of stock. Dropping it would hide why an item was or was not offered.
- **Guard:** Only a figure the service used to decide is allowed, never another service's representation. Product names, prices and availability queries stay with products and stock, and clients fetch them by id.
- **Services:** [recommendations](./recommendations.md)
- **Decided in:** [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (Decision (no composition))

### E-07 — recommendations depends on seven published contracts

- **What:** One service reads seven boundaries: products, categories, stock, customers, preferences, sales and segments.
- **Why it is acceptable:** RN-40 is a rule over all of them. Splitting it would put one rule in several services, or copy seven datasets into projections that drift.
- **Guard:** It reads versioned `api_*` views only, never a base table, and names the columns it reads. A breaking change ships `_v2` beside `_v1`, and the old object is dropped only when `grants.csv` shows no consumer.
- **Services:** [recommendations](./recommendations.md)
- **Decided in:** [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (Reads: the published database contracts)

### E-08 — read-rule services count as one-rule services

- **What:** Four services write no invariant of their own. They enforce a read rule: what they may report, and to whom.
- **Why it is acceptable:** Counting them follows the instructor's wording (one business rule per service), and the domain does not offer more writers: segmentation runs belong to the web (RN-05), and the audit log is written by `fn_audit`.
- **Guard:** Q-13 asks the instructor to confirm it in writing. If the answer is no, the floor still holds: ten core writers plus campaigns. ADR-0043 forbids cutting any core service.
- **Services:** [stock](./stock.md), [segments](./segments.md), [recommendations](./recommendations.md), [audit-trail](./audit-trail.md)
- **Decided in:** [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (What counts, and the margin if Q-13 goes against it)

### E-09 — the frozen web is a legacy co-writer of some tables

- **What:** The web still writes `app_user`, `product`, `category`, `campaign`, `transaction`, `transaction_line` and, through its CSV path, `inventory`. "Only service writer" is not "only writer".
- **Why it is acceptable:** The web is frozen, yet a phone sale must appear in it and its edits must keep working. Services therefore write the same `public` tables the web reads.
- **Guard:** Rules both enforce live in the schema (CHECKs, unique indexes, triggers) or are pinned by golden fixtures (E-17). The owner's outbox trigger captures web writes too, so no writer is invisible.
- **Services:** [users](./users.md), [products](./products.md), [categories](./categories.md), [campaigns](./campaigns.md), [sales](./sales.md), [stock](./stock.md)
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Context); [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (Events)

### E-10 — read owners of tables only the web writes

- **What:** Some services read a table they never write (`role`, `store`, `inventory`, `channel`, the segmentation tables, `audit_log`) and publish it through a contract.
- **Why it is acceptable:** Without a declared owner, several services would read `store` directly and nobody would know who a change breaks. One read owner gives one service to change and one contract for everyone else.
- **Guard:** Exactly one `svc_*` role holds any privilege on each base table, and CI checks it. A read owner cannot fix its own data: the web does.
- **Services:** [users](./users.md), [stores](./stores.md), [stock](./stock.md), [sales](./sales.md), [segments](./segments.md), [audit-trail](./audit-trail.md)
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Decision and ownership matrix)

### E-11 — foreign keys cross service boundaries

- **What:** Tables owned by different services reference each other (`customer.channel_id`, `transaction_line.product_id`, `campaign.label_code` to `segment_label`, `checkins.store_check_in` to `store` and `app_user`).
- **Why it is acceptable:** They come from the legacy schema or protect it. The RI check runs as the parent's owner, so the child's service needs no privilege on the parent. Dropping them would weaken the model the web relies on.
- **Guard:** A violation (23503) maps to a 422 by constraint name. No service holds DELETE on a master, so no cascade starts from a service. The category guard covers the remaining case.
- **Services:** [customers](./customers.md), [customer-preferences](./customer-preferences.md), [sales](./sales.md), [check-ins](./check-ins.md), [stores](./stores.md), [campaigns](./campaigns.md)
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Consequences)

### E-12 — audit_log is the one table every writer shares

- **What:** Every audited write, from any service or the web, lands in `audit_log` through `fn_audit`. audit-trail reads it.
- **Why it is acceptable:** The audit is a cross-cutting append-only record (RN-28 to RN-30). One writer function keeps it uniform and attributable, and services pass the actor with `set_config('mosaiq.user_id', <sub>, true)`.
- **Guard:** `fn_audit` is SECURITY DEFINER, owned by `definer_audit`. No `svc_*` role writes the table, and only `svc_audit_trail` reads it.
- **Services:** [audit-trail](./audit-trail.md), [users](./users.md), [products](./products.md), [categories](./categories.md), [stores](./stores.md), [customers](./customers.md), [campaigns](./campaigns.md)
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Compliance); [ADR-0046](../adr/0046-service-owned-data-lives-in-service-schemas.md) (The tables)

### E-13 — a definer guard reads rows its service cannot read

- **What:** Deleting a category is refused when customers name it as an interest, a fact owned by customer-preferences. Activating a campaign is refused while an attached experiment is not ready (RN-24), a fact held in the web-only `experiment` and `experiment_group` tables.
- **Why it is acceptable:** `customer_interest_category` cascades on a category delete. Without the guard, a categories delete would remove another boundary's rows. The refusal protects customer-preferences' data without writing it. The web refuses to activate a campaign whose experiment has no treatment arm; without the guard, campaigns would activate it, because `svc_campaigns` may not read the experiment tables.
- **Guard:** `trg_category_delete_guard` and `trg_campaign_activation_guard` read with `definer_guard` (SELECT on `customer_interest_category`, `experiment` and `experiment_group` only), and neither service gains a privilege on those tables. The delete guard applies to `svc_*` sessions and the activation guard to `svc_campaigns` sessions only, so the web's cascade and its own activation check are unchanged.
- **Services:** [categories](./categories.md), [campaigns](./campaigns.md)
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Ownership and grants (no cascade, activation guard)); [ADR-0045](../adr/0045-services-never-import-the-web-system.md) (How a shared rule is carried)

### E-14 — campaigns shares the web's id-allocation lock

- **What:** `campaign_id` is allocated as max+1 under `pg_advisory_xact_lock(221001)`, the key the web uses.
- **Why it is acceptable:** Both writers allocate ids the same way. Using the same lock keeps them from drawing the same number.
- **Guard:** The key is recorded under `contracts/db/` next to the table, the service retries on 23505, and the primary key backstops any slip.
- **Services:** [campaigns](./campaigns.md)
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Ownership and grants)

### E-15 — the credential view carries the password hash

- **What:** `api_users.credential_v1` exposes `password_hash` to auth, a different service from the one that writes it.
- **Why it is acceptable:** auth must verify argon2id hashes to authenticate, and the hash lives in `app_user`, owned by users. A copy would be a second source of credentials.
- **Guard:** It is the only view that carries a credential, and auth is its only consumer, checked by `grants.csv`. The hash never appears in a payload, an event or a log.
- **Services:** [auth](./auth.md), [users](./users.md)
- **Decided in:** [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (Reads: the published database contracts); [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md) (Sign-in and passwords)

### E-16 — phone sales share the transaction id namespace with CSV

- **What:** Phone sales and CSV rows both write `transaction`, and share the unique `source_transaction_id`. Phone sales use the prefix `svc-sales:`.
- **Why it is acceptable:** A single unique column lets the web's lookup by source id keep working, and makes phone sales idempotent in PostgreSQL alone.
- **Guard:** Two BEFORE INSERT triggers refuse the prefix unless the session user is `svc_sales`, and refuse lines on a namespace header. A CSV row that names the prefix is rejected as a malformed row, with no web change.
- **Services:** [sales](./sales.md)
- **Decided in:** [ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md) (The reserved namespace)

### E-17 — rules ported from the web exist twice

- **What:** Four rules exist in the web and in a service: sale validation, the RN-40 recommendation rule, user outcomes and campaign transitions.
- **Why it is acceptable:** A service may not import `web/`, which is frozen, loads Flask and has no version to pin. Carrying a rule twice is the price of independence.
- **Guard:** The database carries what it can (CHECKs, indexes, triggers). The rest is a port pinned by golden fixtures captured once from the web on the seed database, with a drift job. A web bug fix opens an issue to regenerate them.
- **Services:** [sales](./sales.md), [recommendations](./recommendations.md), [users](./users.md), [campaigns](./campaigns.md)
- **Decided in:** [ADR-0045](../adr/0045-services-never-import-the-web-system.md) (Decision); [ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md) (Context)

### E-18 — "never deleted" is enforced in service code, not in MongoDB

- **What:** The MongoDB user holds the built-in `readWrite` role, which includes `remove`. Events are voided, never deleted, by convention in the service.
- **Why it is acceptable:** A custom role without `remove` would fail the rollup rebuild and the start-up index checks with authorization errors. The boundary that matters for independence is one user on one database.
- **Guard:** A CI grep forbids delete calls in the service. The ADR names this the weakest point of the decision and revisits it if MongoDB ever holds anything sensitive.
- **Services:** [engagement](./engagement.md)
- **Decided in:** [ADR-0038](../adr/0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md) (Alternatives considered)

### E-19 — one PostgreSQL, one DDL file, one redis-state, one MongoDB

- **What:** All services share one PostgreSQL database, one DDL file, one `redis-state`, one `redis-cache` and one MongoDB server. Independence is by role, schema and prefix, not by instance.
- **Why it is acceptable:** The course requires one DDL file that runs clean from empty on one 8 GB VM, with no managed database, and the web must see what services write.
- **Guard:** One `svc_*` role per service, one ACL user per service limited to its prefixes, one MongoDB user on one database, and a memory budget CI checks. Each boundary's schema section is idempotent and deploys expand-before-deploy.
- **Services:** all 15 services
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Alternatives considered); [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) (Instances); [ADR-0040](../adr/0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md) (Decision)

### E-20 — the liveness key and the JWKS file are read by every service

- **What:** Every service reads `pub:auth:v1:live:{sid}` and the mounted `/run/mosaiq/jwks.json`, both published by auth.
- **Why it is acceptable:** Authentication must be checked on every request, locally, without HTTP between services.
- **Guard:** The Redis selector allows `EXISTS` on that one family and nothing else of `auth:*`. Both are versioned contracts (`contracts/auth/redis-liveness.v1.md`). Redis failure fails closed.
- **Services:** all 15 services
- **Decided in:** [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md) (Verification in every service); [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) (ACLs)

### E-21 — a mechanism-only kit is shared by every service

- **What:** Python services pin `mosaiq-kit` and Rust services pin `kit-rs` by tag: token verification, negotiation, errors, pagination, health, relay and consumer helpers.
- **Why it is acceptable:** Re-implementing security and negotiation fifteen times would be worse for independence and safety than one pinned, versioned mechanism.
- **Guard:** A kit holds no permission names, no rule and no Redis key except the liveness family. Each service pins an exact tag and upgrades in its own pull request, and path dependencies are rejected.
- **Services:** all 15 services
- **Decided in:** [ADR-0045](../adr/0045-services-never-import-the-web-system.md) (Decision)

### E-22 — a bench-only Rust build serves the same read endpoints as products

- **What:** `bench/products-service-rs/` implements the products read operations so the two languages can be compared.
- **Why it is acceptable:** A fair comparison needs the same contract under the same load. Two services doing different work cannot be compared.
- **Guard:** It is never registered, routed, deployed or counted, so no endpoint is served by two deployables. It connects as `svc_products_bench` with SELECT on `public.product` only.
- **Services:** [products](./products.md)
- **Decided in:** [ADR-0042](../adr/0042-a-bench-only-rust-build-of-the-products-reads-is-compared-under-one-load.md) (Decision)

### Adding or changing an exception

A new exception needs an ADR change and an entry in this register **before** it is built. An exception with no guard is not acceptable: if it cannot be checked by a grant, a CI check, a contract rule or a review rule that fails visibly, it is a defect, not an exception. Q-13 in [`scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md) asks the instructor to confirm E-08 and the shared-database model in writing.

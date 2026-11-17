# customer-preferences

> **Status:** Implemented (phase 14, core; F14-16, #475) for the two declared sets, and in phase 21 for consent, restrictions and the preference history (F21-16, #794; F21-17, #795) and for the interests inferred from net purchases (F21-18, #796), which `GET .../interests` serves. ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** A customer's preferred channels and interest categories are each replaced only atomically, as a whole set of existing references, and their consent and restrictions change only by appending a recorded, attributed decision that is never edited or removed (RN-54, RN-73)

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
| Used by | Android: interest capture (F17-04), the permitted profile (F27-03) and consent at registration (F27-09). Electron: both preference sets (F16-03). recommendations, campaigns and rfm-engine read its consent and restriction views. |
| Governing ADRs | [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md), [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md), [ADR-0066](../adr/0066-consent-is-an-append-only-record-by-purpose-and-anonymization-replaces-deletion.md) |

## Business rule

- The two sets are independent multivalued facts (the 4NF decomposition). Each `PUT` replaces one whole set in a single transaction, serialized per customer by `pg_advisory_xact_lock(290001, hashtext(customer_id::text))`.
- References are checked: the customer through `api_customers.customer_ref_v1` (404 if unknown), channels and categories by FK (23503 maps to 422).
- Consent is recorded per purpose (`PERSONALIZATION`, `MARKETING_CONTACT`, `PROFILING`) and restrictions per channel or interest category, each as an append-only log (RN-73, ADR-0066). A decision is a new row; the current state of a purpose is its latest row, a withdrawal never rewrites the grant before it, and the service has no route that edits or removes a row. `recorded_at` is the server's clock and `recorded_by` the app user of the verified token; neither is read from a body. A purpose with no row is `NOT_RECORDED` and is not granted.
- A decision commits with its outbox row in one transaction, so the views `api_preferences.consent_state_v1` and `api_preferences.restriction_v1` show it when `stream:preferences.v1.consent_changed` announces it. The whole-set rule of RN-54 is untouched for the two declared sets; each replace that changes a set also appends its before and after to the preference history.
- Declared and inferred interests are separate facts and separate members of `InterestProfile`. Only the whole-set `PUT` writes the declared set, so an inferred interest can never overwrite a declared one, and a declared category is not repeated among the inferred ones. Inferred interests are returned only while the customer's current `PROFILING` and `PERSONALIZATION` decisions are both `GRANTED`.
- Inference (F21-18, RN-34, RN-35) reads a customer's net purchases through `api_sales.net_purchase_line_v1` (ADR-0062) and the category of each product through `api_products.product_ref_v1`, and replaces the customer's rows of `preferences.inferred_interest` as a whole. An interest states its `source` (`NET_PURCHASES`), its `window_days` (180 unless `INFERENCE_WINDOW_DAYS` says otherwise) and its `strength`: the percentage of the customer's net purchases in the window that include a product of the category, rounded half up. A sale counts once for a category however many of its lines are in it, a sale returned in full is not a purchase, and a category under 1% is not kept. They are listed by strength descending, then by category.
- A customer who objects to profiling gets nothing inferred. A `PROFILING` decision that is not `GRANTED` (withdrawn, or never recorded) stops the run for that customer, and the rows they had are deleted in the transaction that records the withdrawal. A withdrawn `PERSONALIZATION` keeps the rows but the reader returns none (ADR-0066 section 3). The run holds the customer's decision lock (`290002`), so a withdrawal and a run never interleave.
- A declared interest stands. Inference never writes `customer_interest_category`, does not infer a category the customer declared, and the reader leaves a stored row out once its category is declared, so the two members of `InterestProfile` never overlap and each response keeps them apart.
- The run is a background job of the service, not an operation: every `INFERENCE_INTERVAL_HOURS` (24) it infers for every customer who grants `PROFILING` and deletes the rows of any who do not. `INFERENCE_ENABLED=false` turns it off.
- Registration is customers' operation and records no consent. The client follows it with `POST .../consent-decisions`, asking for each purpose separately; until then every purpose is `NOT_RECORDED`, which no reader treats as granted. A customer anonymized by customers-service (ADR-0066 section 3) accepts no new decision: 409 `customer_anonymized`.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/preference-profiles/{customer_id}` | Channel ids and declared category ids of one customer (E-03) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR, LOYALTY_OFFICER |
| PUT | `/v1/preference-profiles/{customer_id}/preferred-channels` | Replace the whole set of preferred channels | ADMIN, STORE_MANAGER, LOYALTY_OFFICER |
| PUT | `/v1/preference-profiles/{customer_id}/interest-categories` | Replace the whole set of declared interest categories | ADMIN, STORE_MANAGER, LOYALTY_OFFICER |
| GET | `/v1/preference-profiles/{customer_id}/interests` | Declared and inferred interests, in separate members; not audited | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR, SALES_ASSOCIATE, LOYALTY_OFFICER |
| GET | `/v1/preference-profiles/{customer_id}/restrictions` | Current restricted channels and categories, and whether personalization is allowed | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR, SALES_ASSOCIATE, LOYALTY_OFFICER |
| GET | `/v1/preference-profiles/{customer_id}/consent` | State of the three purposes, now or at `as_of` | ADMIN, AUDITOR, STORE_MANAGER, LOYALTY_OFFICER |
| GET | `/v1/preference-profiles/{customer_id}/consent-decisions` | The consent log, newest first (`purpose`, `page`, `size`); audited as `HISTORY_READ` | ADMIN, AUDITOR, STORE_MANAGER, LOYALTY_OFFICER |
| POST | `/v1/preference-profiles/{customer_id}/consent-decisions` | Append one to three consent decisions; 201 with the state | ADMIN, LOYALTY_OFFICER |
| GET | `/v1/preference-profiles/{customer_id}/restriction-decisions` | The restriction log, newest first (`kind`, `page`, `size`); audited as `HISTORY_READ` | ADMIN, AUDITOR, STORE_MANAGER, LOYALTY_OFFICER |
| POST | `/v1/preference-profiles/{customer_id}/restriction-decisions` | Append up to 50 restriction decisions; 201 with the current restrictions | ADMIN, LOYALTY_OFFICER |
| GET | `/v1/preference-profiles/{customer_id}/preference-changes` | Before and after of each replace of a declared set (`preference_set`, `page`, `size`) | ADMIN, AUDITOR, STORE_MANAGER, LOYALTY_OFFICER |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `customer_preferred_channel` | Table (`public`) | S: SELECT, INSERT, DELETE (whole-set replace) | The web only reads it |
| `customer_interest_category` | Table (`public`) | S: SELECT, INSERT, DELETE (whole-set replace) | The web only reads it |
| `preferences.consent_decision` | Table (`preferences`) | S: SELECT, INSERT | Append-only for every role (privileges and a trigger). 103 seed rows: the ADR-0066 default of every customer, and staff decisions on top |
| `preferences.restriction_decision` | Table (`preferences`) | S: SELECT, INSERT | Append-only. 34 seed rows, 4 of them lifts |
| `preferences.preference_change`, `preferences.preference_change_member` | Tables (`preferences`) | S: SELECT, INSERT | Append-only; one change per replace that changed a set, with its before and after ids as member rows. 60 and 120 seed rows |
| `preferences.inferred_interest` | Table (`preferences`) | S: SELECT, INSERT, DELETE (a run replaces a customer's set whole) | Derived, never edited. Primary key `(customer_id, category_id)`; 276 seed rows computed from the seeded sales for the 30 customers who grant `PROFILING` |
| `preferences_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_preferences`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_preferences.interest_category_v1` | recommendations (declared interests only) |
| Publishes | `api_preferences.consent_state_v1` | recommendations, campaigns, rfm-engine (its grant follows with its role) |
| Publishes | `api_preferences.restriction_v1` | recommendations, campaigns |
| Publishes | `public.consent_state` | The web's batch segmentation, read-only (ADR-0066 section 2) |
| Consumes | `api_customers.customer_ref_v1` | customers |
| Consumes | `api_sales.net_purchase_line_v1` | sales (inference input, F21-18) |
| Consumes | `api_products.product_ref_v1` | products (category of each purchased product, F21-18) |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:preferences.v1.changed` | captured from both preference tables | recommendations' cache |
| Produces | `stream:preferences.v1.consent_changed` | captured from `preferences.consent_decision` and `preferences.restriction_decision` | `recommendations`, `campaigns` and `rfm-engine` re-read the views for the customer |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `preferences:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 404 | `customer_not_found` | The customer does not exist in `api_customers.customer_ref_v1` |
| 409 | `customer_anonymized` | A decision is recorded for an anonymized customer (planned: it needs the anonymized flag of `api_customers.customer_ref_v1`, F21-19, #797) |
| 503 | `audit_unavailable` | A read of the consent or restriction log cannot record its `HISTORY_READ` event |
| 422 | `invalid_query` | `as_of` or a filter is malformed, or `as_of` is in the future |
| 422 | `invalid_body` | A purpose, or a `(kind, target_id)` pair, repeats in one call (`duplicate`) |
| 422 | `unknown_channel` | A channel id references no channel (23503), in a set or a restriction |
| 422 | `unknown_category` | A category id references no category (23503), in a set or a restriction |

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
| PostgreSQL unreachable | `/health/ready` reports not ready, and every operation that reads or writes PostgreSQL answers 503 `database_unavailable`, never 500 (ADR-0035). `/health/live` stays 200 |
| `redis-state` full | `XADD` of `stream:preferences.v1.changed` or `stream:preferences.v1.consent_changed` fails; the outbox keeps the rows until there is room |
| The domain event of an audited read cannot be recorded | A read of the consent or restriction log answers 503 `audit_unavailable` and returns nothing (ADR-0072 section 4); a database that is down stays 503 `database_unavailable` |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) Compliance (two tables, one service role).
- [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) Compliance (outbox capture, stream ownership).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-15 contract, F14-16 implementation; consumed by F17-04, F16-03. F21-16 (#794) publishes the consent, restriction, inferred-interest and history contract, F21-17 (#795) implements it and F21-18 (#796) infers interests and serves `GET .../interests`. Issue numbers live in [`docs/backlog.md`](../backlog.md).

# sales

> **Status:** Implemented (phase 14, core; F14-18, #454). ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** A sale is recorded exactly once, all or nothing, consuming its stock in the same transaction, and only with lines dated not in the future and not before the customer's registration, for active products, with positive quantities and positive two-decimal prices (RN-11 to RN-13, RN-37 (accepted sales), RN-70)

## Summary

| Field | Value |
|---|---|
| Language | Flask |
| Port (loopback) | 5009 |
| Public path | `/api/sales/v1/` |
| Wave | core |
| PostgreSQL role | `svc_sales` |
| Redis ACL user | `svc_sales`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-sales` / `ghcr.io/equipo-03-proyecto10/mosaiq-sales` |
| Used by | Android: channel list, `POST /v1/sales` with an offline queue, status (F17-04). Electron: sales read-back in XML (F16-04). |
| Governing ADRs | [ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md), [ADR-0046](../adr/0046-service-owned-data-lives-in-service-schemas.md) |

## Business rule

- One PostgreSQL transaction per `POST /v1/sales`: validate the whole sale, insert the header and every line, call `api_stock.consume_v1` per line in ascending `product_id` order, insert `sales.sale_capture`, commit. Any refusal rolls everything back.
- Idempotency is durable in PostgreSQL: the `Idempotency-Key` becomes `source_transaction_id = 'svc-sales:' || key`, and a request hash decides between a replay (200 with the original) and a conflict (409). No Redis key is involved.
- A reversal (RN-70, ADR-0062) is its own transaction: lock the sold lines in ascending product order, record the `sales.sale_reversal` header and its lines, add to `transaction_line.returned_quantity`, call `api_stock.restock_v1` per resellable line in ascending product order, commit. The outbox row for `stream:sales.v1.reversed` is written by the capture trigger in that transaction, so a rollback publishes nothing. A sale is never edited or deleted; the database refuses reversals that add up to more than was sold. Its time limits are configuration (`SALES_CANCELLATION_WINDOW_DAYS`, `SALES_RETURN_WINDOW_DAYS`, `SALES_TIMEZONE`); the store narrowing of ADR-0060 is #757's.
- An import (RN-37, ADR-0070) is a second way in for the same rule. `POST /v1/sale-imports` opens it, `PUT /v1/sale-imports/{id}/chunks/{n}` stages numbered chunks of at most 150 rows and answers 202, and the import worker, a second process of this image (`python -m app.import_worker`, compose service `sales-import-worker`, 96 MB), applies the rows one at a time. Each row passes `salerules.validate_sale_rules` and `validate_sale_references`, the same functions `POST /v1/sales` calls, and takes stock through the same `store.consume_stock`. Its outcome (accepted with the sale line it made, or rejected with line, field, code and sentence) commits with the row, so a resume skips every line that has one. Counts reconcile: `received = accepted + rejected + pending`, and `pending` is zero when `COMPLETED`. A repeated file creates no sale twice: a row repeating a recorded line is `duplicate_row`, told apart as `FILE` or `RECORDED_SALE`.
- The service never imports the web's ingestion code. The rules it shares with the CSV path are carried by the database and by golden fixtures (E-17). Serves the read-only channel vocabulary (E-01).

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| POST | `/v1/sales` | Record a sale (`Idempotency-Key` UUID required); 201, or 200 with `Idempotent-Replayed: true` on a replay | ADMIN, STORE_MANAGER |
| GET | `/v1/sales` | List sales (`customer_id`, `store_id`, `from`, `to`, `page`, `size`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| GET | `/v1/sales/{sale_id}` | One sale (status; the Electron read-back) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| POST | `/v1/sale-reversals` | Record a cancellation or a return (`Idempotency-Key` UUID required); 201, or 200 with `Idempotent-Replayed: true` on a replay. A seller records cancellations only | ADMIN, STORE_MANAGER, SALES_ASSOCIATE (returns: ADMIN, STORE_MANAGER) |
| GET | `/v1/sale-reversals` | List reversals (`sale_id`, `customer_id`, `store_id`, `kind`, `from`, `to`, `page`, `size`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR, SALES_ASSOCIATE, COMMERCIAL_SUPERVISOR |
| GET | `/v1/sale-reversals/{reversal_id}` | One reversal with its lines | same as the list |
| POST | `/v1/sale-imports` | Open an import (`Idempotency-Key` UUID required); 201, or 200 with `Idempotent-Replayed: true`. One import is open at a time | ADMIN |
| GET | `/v1/sale-imports` | The recent imports, newest first | ADMIN |
| GET | `/v1/sale-imports/{import_id}` | One import: status, `next_sequence` and the reconciled counts | ADMIN |
| PUT | `/v1/sale-imports/{import_id}/chunks/{sequence}` | Stage one numbered chunk; 202, or 200 with `Idempotent-Replayed: true` for the same chunk again | ADMIN |
| GET | `/v1/sale-imports/{import_id}/rejections` | The refused rows, by line, with field, code and reason | ADMIN |
| PUT | `/v1/sale-imports/{import_id}/status` | Cancel an import (`CANCELLED` is the only value) | ADMIN |
| GET | `/v1/channels` | Channel vocabulary, read only (E-01) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, INVENTORY_PLANNER, AUDITOR |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `transaction`, `transaction_line` | Tables (`public`) | S+W: SELECT, INSERT | The web's CSV path is a legacy co-writer (E-09); the `svc-sales:` namespace is guarded (E-16) |
| `transaction_line.returned_quantity` | Column (`public`) | S: UPDATE of this column only | The running total of a line's reversals; CHECK between 0 and `quantity`; a trigger refuses any other writer (ADR-0062) |
| `sales.sale_reversal`, `sales.sale_reversal_line` | Tables (`sales`) | S: SELECT, INSERT | New; append-only; the database refuses reversals that exceed the sale; seed-exempt runtime state |
| `transaction.total` | Column (`public`) | S: UPDATE of this column only | An import appends lines to a header and recomputes its total, as the web's CSV path does |
| `sales.import`, `sales.import_chunk`, `sales.import_row_outcome` | Tables (`sales`) | S: SELECT, INSERT, UPDATE (outcomes: SELECT, INSERT) | New; the control state of an import; seed-exempt runtime state |
| `sales.sale_capture` | Table (`sales`) | S: SELECT, INSERT | New; `transaction_id` PK, `request_sha256`, `recorded_by`, `captured_at`; seed-exempt runtime state |
| `channel` | Table (`public`) | R: SELECT | Only the web writes it (E-10) |
| `sales_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_sales`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_sales.purchase_line_v1` | recommendations |
| Publishes | `api_sales.net_purchase_line_v1` | recommendations (SELECT granted); the net definition of ADR-0062, also read by the web as `public.net_sale_line` |
| Consumes | `api_products.product_ref_v1` | products |
| Consumes | `api_customers.customer_ref_v1` | customers |
| Calls | `api_stock.consume_v1(smallint, int, int)` | stock (EXECUTE; E-05) |
| Calls | `api_stock.restock_v1(smallint, int, int, bigint)` | stock (EXECUTE; E-05): once per resellable reversed line, in the reversal's transaction |
| Calls | `api_audit.record_event_v1(...)` | audit-trail (EXECUTE; ADR-0072): a `HISTORY_READ` for `GET /v1/sales?customer_id=`, a 403 included, in the read's transaction; 503 `audit_unavailable` when it cannot |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:sales.v1.recorded` | captured from `transaction` AFTER INSERT OR UPDATE OF `total`, for the service and the CSV path | recommendations' cache; consumers dedupe by `event_id` |
| Produces | `stream:sales.v1.reversed` | captured from `sales.sale_reversal` AFTER INSERT, one event per reversal (`sale_id:reversal_id`) | recommendations, rfm-engine and experimentation recompute for the customer; consumers dedupe by `event_id` |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `sales:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 400 | `idempotency_key_required` | The key is missing or not a UUID |
| 409 | `idempotency_key_reused` | The same key with a different request hash |
| 409 | `stock_unavailable` | A line cannot be consumed; names the line |
| 409 | `duplicate_product_line` | A `product_id` appears twice |
| 422 | `no_lines` | The sale has no lines |
| 422 | `no_stock_record` | No inventory row for the store and product |
| 422 | `unknown_customer`, `unknown_store`, `unknown_channel`, `unknown_product` | A reference does not exist |
| 422 | `inactive_product`, `before_registration`, `future_dated`, `invalid_price`, `invalid_quantity` | A sale rule is broken |
| 404 | `sale_not_found` | `GET /v1/sales/{sale_id}` names no sale |
| 404 | `sale_import_not_found` | An import route names no import |
| 409 | `import_in_progress`, `import_not_receiving`, `import_finished` | Another import is open; the import takes no more chunks; it is `COMPLETED` or `EXPIRED` and cannot be cancelled |
| 409 | `chunk_conflict`, `chunk_out_of_order` | The same chunk with other rows; a chunk above `next_sequence` |
| 422 | `row_count_mismatch` | The rows of the chunks do not add up to `total_rows` |
| (row) | `wrong_cell_count`, `invalid_value`, `missing_offset`, `formula_like_id`, `id_too_long`, `header_mismatch`, `duplicate_row` | Only in a rejection report, never a response status: a row of a file refused, plus the codes of `POST /v1/sales` |
| 404 | `sale_reversal_not_found` | `GET /v1/sale-reversals/{reversal_id}` names no reversal |
| 403 | `forbidden_role` | A `SALES_ASSOCIATE` records a return (a seller only cancels), or a role outside `x-mosaiq-roles` |
| 409 | `quantity_exceeds_remaining` | A return asks for more units than the sale still holds (nothing changes) |
| 409 | `sale_already_reversed` | A cancellation of a sale that already has a reversal |
| 409 | `reversal_window_closed` | The cancellation or return window has passed (configuration) |
| 422 | `unknown_sale`, `unknown_sale_line` | The sale, or a product on it, does not exist |
| 422 | `reason_required`, `no_return_lines`, `unexpected_lines` | A reason is missing, a return has no lines, a cancellation has some |
| 422 | `no_stock_record` | A reversed line's store has no stock record: nothing is recorded |
| 422 | `invalid_query` | `GET /v1/sales` has a malformed `customer_id`, `store_id`, `from` or `to`, or `from` after `to` |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-01 — sales serves the read-only channel vocabulary

- **What:** sales-service serves `GET /v1/channels`, the list of sales channels. Strictly, a channel is not a sale.
- **Why it is acceptable:** The sales rule validates every sale's `channel_id` against this vocabulary, so sales already reads it, and the Android client must offer it when it records a sale. Only the web writes `channel` (five rows). A separate service for five read-only rows would be a filler service, which the instructor excluded.
- **Guard:** The operation is GET only and registered to sales alone, so no other service may serve `channels`. ADR-0043 limits the exception to a vocabulary the serving service's own rule validates against. A write endpoint would make it a second rule.
- **Decided in:** [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (Decision); [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Registry). Register: [E-01](./README.md#e-01--sales-serves-the-read-only-channel-vocabulary).

### E-05 — one synchronous cross-boundary call, inside the database

- **What:** sales-service calls `api_stock.consume_v1` once per sale line, inside the sale's transaction, and `api_stock.restock_v1` once per resalable reversed line, inside the reversal's transaction (ADR-0061, ADR-0062). They are the only places where sales calls into another boundary.
- **Why it is acceptable:** A sale and its stock decrement must commit together (RN-14, ADR-0031), and so must a reversal and its restock (ADR-0061). A saga or an HTTP call to stock could leave a sale without its decrement, a reversal without its restock, or the reverse. A SECURITY DEFINER function owned by `definer_stock` gives atomicity without a stock write privilege for sales.
- **Guard:** EXECUTE on both goes to `svc_sales` only, and the grant check compares it both ways. Both are in the database and never HTTP. ADR-0044 states that a further cross-boundary write needs its own record; for `restock_v1`, that record is ADR-0061.
- **Decided in:** [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (The one synchronous call); [ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md) (The transaction). Register: [E-05](./README.md#e-05--one-synchronous-cross-boundary-call-inside-the-database).

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

### E-11 — foreign keys cross service boundaries

- **What:** Tables owned by different services reference each other (`customer.channel_id`, `transaction_line.product_id`, `campaign.label_code` to `segment_label`, `checkins.store_check_in` to `store` and `app_user`).
- **Why it is acceptable:** They come from the legacy schema or protect it. The RI check runs as the parent's owner, so the child's service needs no privilege on the parent. Dropping them would weaken the model the web relies on.
- **Guard:** A violation (23503) maps to a 422 by constraint name. No service holds DELETE on a master, so no cascade starts from a service. The category guard covers the remaining case.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Consequences). Register: [E-11](./README.md#e-11--foreign-keys-cross-service-boundaries).

### E-16 — phone sales share the transaction id namespace with CSV

- **What:** Phone sales and CSV rows both write `transaction`, and share the unique `source_transaction_id`. Phone sales use the prefix `svc-sales:`.
- **Why it is acceptable:** A single unique column lets the web's lookup by source id keep working, and makes phone sales idempotent in PostgreSQL alone.
- **Guard:** Two BEFORE INSERT triggers refuse the prefix unless the session user is `svc_sales`, and refuse lines on a namespace header. The `transaction` trigger also refuses a row from `svc_sales` whose `data_origin` is not `OBSERVED` (ADR-0052, #559), so the header INSERT must name the column and bind `OBSERVED`; that 22023 is a service defect, not a client error. A CSV row that names the prefix is rejected as a malformed row, with no web change.
- **Decided in:** [ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md) (The reserved namespace). Register: [E-16](./README.md#e-16--phone-sales-share-the-transaction-id-namespace-with-csv).

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
| `redis-state` down | Sales are refused with 503 `auth_unavailable` like any authenticated call. Idempotency does not depend on Redis, so a retry after recovery cannot create a second sale |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| `redis-state` full or down | The relay cannot `XADD`; unrelayed outbox rows wait until there is room, and consumers' caches expire by TTL |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md) Compliance (replay, conflict, namespace probe, a short multi-line sale leaves nothing).
- [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) Compliance (`consume_v1` executable by `svc_sales` only).
- Golden fixtures for the ported sale rules ([ADR-0045](../adr/0045-services-never-import-the-web-system.md)).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-17 contract, F14-18 implementation; consumed by F17-04, F16-04. Issue numbers live in [`docs/backlog.md`](../backlog.md).

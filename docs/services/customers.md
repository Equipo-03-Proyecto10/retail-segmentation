# customers

> **Status:** Planned (phase 14, core). Not built until ADR-0034 to ADR-0046 are accepted (F13-01, #408).
> **One rule:** A customer is registered once, with a valid unique email when given, a known channel and a registration date not in the future, and is never deleted through services (RN-08)

## Summary

| Field | Value |
|---|---|
| Language | Flask |
| Port (loopback) | 5007 |
| Public path | `/api/customers/v1/` |
| Wave | core |
| PostgreSQL role | `svc_customers` |
| Redis ACL user | `svc_customers`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-customers` / `ghcr.io/equipo-03-proyecto10/mosaiq-customers` |
| Used by | Android: search and register at the counter (F17-04). Electron: customer screen (F16-03). |
| Governing ADRs | [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) |

## Business rule

- Email is unique when given (409 `email_taken`), `channel_id` references a known channel (FK; 23503 maps to 422 `unknown_channel`) and `registered_on` is not in the future.
- No DELETE grant: sales and segment history hang on the record (RN-08), so a mistaken registration is corrected with PATCH. `q` search matches `%` and `_` literally.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/customers` | Search (`q` over name, email or phone; `email`, `page`, `size`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| GET | `/v1/customers/{customer_id}` | One customer | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| POST | `/v1/customers` | Register a customer | ADMIN, STORE_MANAGER |
| PATCH | `/v1/customers/{customer_id}` | Correct a customer's data | ADMIN, STORE_MANAGER |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `customer` | Table (`public`) | S: SELECT, INSERT, UPDATE of `name`, `email`, `phone`, `registration_channel_id`; no DELETE | The web only reads it; audited (E-12) |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_customers.customer_ref_v1` | sales, customer-preferences, segments, recommendations, engagement |

## Events

None.

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `customers:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 409 | `email_taken` | The email already belongs to another customer |
| 422 | `unknown_channel` | `channel_id` references no channel (23503) |
| 422 | Undecided; the contract story fixes the code | A future `registered_on` (F14-13 fixes the code) |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-11 — foreign keys cross service boundaries

- **What:** Tables owned by different services reference each other (`customer.channel_id`, `transaction_line.product_id`, `campaign.label_code` to `segment_label`, `checkins.store_check_in` to `store` and `app_user`).
- **Why it is acceptable:** They come from the legacy schema or protect it. The RI check runs as the parent's owner, so the child's service needs no privilege on the parent. Dropping them would weaken the model the web relies on.
- **Guard:** A violation (23503) maps to a 422 by constraint name. No service holds DELETE on a master, so no cascade starts from a service. The category guard covers the remaining case.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Consequences). Register: [E-11](./README.md#e-11--foreign-keys-cross-service-boundaries).

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

## Failure behaviour

| Dependency down | Effect |
|---|---|
| PostgreSQL unreachable | `/health/ready` reports not ready, and every operation that reads or writes PostgreSQL answers 503 `database_unavailable`, never 500 (ADR-0035). `/health/live` stays 200 |
| No cache | Customers are read from PostgreSQL every time; only PostgreSQL and `redis-state` matter |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) Compliance (no DELETE on `customer`; at most one service role on the table).
- Conformance suite: the search treats `%` and `_` literally.
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-13 contract, F14-14 implementation; consumed by F17-04, F16-03. Issue numbers live in [`docs/backlog.md`](../backlog.md).

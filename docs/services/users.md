# users

> **Status:** Planned (phase 14, core). ADR-0034 to ADR-0046 accepted on  (F13-01, #408).
> **One rule:** Accounts obey the single-administrator rule (never two, never zero, moved only by an explicit transfer), have case-insensitively unique emails, and are deactivated, never deleted (RN-01, RN-02, RN-04)

## Summary

| Field | Value |
|---|---|
| Language | Flask |
| Port (loopback) | 5002 |
| Public path | `/api/users/v1/` |
| Wave | core |
| PostgreSQL role | `svc_users` |
| Redis ACL user | `svc_users`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-users` / `ghcr.io/equipo-03-proyecto10/mosaiq-users` |
| Used by | Android: who is signed in, `GET /v1/users/me` (F17-01). Electron: user administration and administrator transfer (F16-06). |
| Governing ADRs | [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md), [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) |

## Business rule

- RN-01 is enforced twice: in service code (a second `ADMIN` is refused, the only `ADMIN` is never deactivated or demoted, and the role moves only through `POST /v1/administrator-transfers`) and by the partial unique index `ux_app_user_single_administrator`, whose 23505 maps to 409 `single_administrator`, never 500.
- Email is unique case-insensitively (`ux_app_user_email_lower`, RN-02). Accounts are deactivated, never deleted: no DELETE grant (RN-04).
- Hashes passwords with argon2id here, one at a time (224 MB). Serves the read-only role vocabulary (E-02).

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/users` | List users (`role`, `active`, `q`, `page`, `size`) | ADMIN, AUDITOR |
| POST | `/v1/users` | Create a user; an `ADMIN` is refused with 409 | ADMIN |
| GET | `/v1/users/me` | The caller's own profile | Every authenticated role, `CUSTOMER` included |
| GET | `/v1/users/{user_id}` | One user | ADMIN, AUDITOR |
| PATCH | `/v1/users/{user_id}` | Edit name, email or a non-`ADMIN` role | ADMIN |
| PUT | `/v1/users/{user_id}/password` | Change the caller's own password (current password required), or an `ADMIN` reset of another user | Every authenticated role for the caller's own id; `ADMIN` for another user |
| POST | `/v1/users/{user_id}/deactivation` | Deactivate; 409 for the only `ADMIN` | ADMIN |
| DELETE | `/v1/users/{user_id}/deactivation` | Reactivate | ADMIN |
| GET | `/v1/roles` | Role vocabulary, read only (E-02) | ADMIN, AUDITOR |
| GET | `/v1/roles/{role_code}` | One role | ADMIN, AUDITOR |
| POST | `/v1/administrator-transfers` | Demote and promote in one transaction under a row lock on the current `ADMIN` | ADMIN |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `app_user` | Table (`public`) | S+W: SELECT, INSERT, UPDATE of `name`, `email`, `role_id`, `is_active`, `password_hash`; no DELETE | The web is a legacy co-writer (E-09) |
| `role` | Table (`public`) | R: SELECT | Only the web writes it; codes are immutable |
| `users_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_users`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_users.credential_v1` | auth (its only consumer; E-15) |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:users.v1.access_changed` | captured from `app_user` UPDATE OF `is_active`, `role_id`, `password_hash`, and DELETE | auth ends the user's sessions |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `users:rl:pwd:{user_id}` | 15 min window | 5 failed current-password checks, then 429 |
| state | `users:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 409 | `single_administrator` | Creating a second `ADMIN`, or the index refusing it (23505) |
| 409 | `use_administrator_transfer` | A `PATCH` that would promote to or demote from `ADMIN` |
| 409 | `administrator_required` | Deactivating the only `ADMIN` |
| 409 | `email_taken` | Email already used (case-insensitively) |
| 429 | `rate_limited` | More than 5 failed current-password checks in 15 min |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-02 — users serves the read-only role vocabulary

- **What:** users-service serves `GET /v1/roles` and `GET /v1/roles/{role_code}`, the seven role codes.
- **Why it is acceptable:** The single-administrator rule is defined over the `ADMIN` role, and every account carries a role, so users already validates role codes. Only the web writes `role`, whose codes are immutable (`trg_role_code_immutable`).
- **Guard:** GET only, registered to users alone, and bound by the same ADR-0043 limit as E-01. Role codes are never created or edited through services.
- **Decided in:** [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (Decision); [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Registry). Register: [E-02](./README.md#e-02--users-serves-the-read-only-role-vocabulary).

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

### E-12 — audit_log is the one table every writer shares

- **What:** Every audited write, from any service or the web, lands in `audit_log` through `fn_audit`. audit-trail reads it.
- **Why it is acceptable:** The audit is a cross-cutting append-only record (RN-28 to RN-30). One writer function keeps it uniform and attributable, and services pass the actor with `set_config('mosaiq.user_id', <sub>, true)`.
- **Guard:** `fn_audit` is SECURITY DEFINER, owned by `definer_audit`. No `svc_*` role writes the table, and only `svc_audit_trail` reads it.
- **Decided in:** [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (Compliance); [ADR-0046](../adr/0046-service-owned-data-lives-in-service-schemas.md) (The tables). Register: [E-12](./README.md#e-12--audit_log-is-the-one-table-every-writer-shares).

### E-15 — the credential view carries the password hash

- **What:** `api_users.credential_v1` exposes `password_hash` to auth, a different service from the one that writes it.
- **Why it is acceptable:** auth must verify argon2id hashes to authenticate, and the hash lives in `app_user`, owned by users. A copy would be a second source of credentials.
- **Guard:** It is the only view that carries a credential, and auth is its only consumer, checked by `grants.csv`. The hash never appears in a payload, an event or a log.
- **Decided in:** [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (Reads: the published database contracts); [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md) (Sign-in and passwords). Register: [E-15](./README.md#e-15--the-credential-view-carries-the-password-hash).

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
| users-service down | No account administration. Sign-in and every session keep working; a web-made access change still reaches auth, because the outbox trigger fires for the web too |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| `redis-state` full or down | The relay cannot `XADD`; unrelayed outbox rows wait until there is room, and consumers' caches expire by TTL |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md) Compliance (single administrator enforced twice, 409 mapping).
- [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) and [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) Compliance (grants: no DELETE on `app_user`; `credential_v1` consumed by auth only).
- Golden fixtures for users outcomes ([ADR-0045](../adr/0045-services-never-import-the-web-system.md)).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-03 contract, F14-04 implementation; consumed by F17-01, F16-06. Issue numbers live in [`docs/backlog.md`](../backlog.md).

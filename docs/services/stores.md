# stores

> **Status:** Planned (phase 14, core). ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** Each store has at most one location, with valid coordinates and a 25–2000 m geofence, set by ADMIN only (RN-53)

## Summary

| Field | Value |
|---|---|
| Language | Rust (axum) |
| Port (loopback) | 5005 |
| Public path | `/api/stores/v1/` |
| Wave | core |
| PostgreSQL role | `svc_stores` |
| Redis ACL user | `svc_stores`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-stores` / `ghcr.io/equipo-03-proyecto10/mosaiq-stores` |
| Used by | Android: store picker (F17-03). Electron: location editor (F16-07). |
| Governing ADRs | [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0046](../adr/0046-service-owned-data-lives-in-service-schemas.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) |

## Business rule

- `stores.store_location` is a 1:1 vertical split of `store` (PK `store_id`), with CHECKs on latitude (−90 to 90), longitude (−180 to 180) and `geofence_radius_m` (25 to 2000), and `set_by` an `ADMIN`.
- stores reads the web-written `store` table as its declared read owner (E-10) and publishes the directory with the location, so a check-in can be verified against it.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/stores` | Directory (`active`, `city`, `page`, `size`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, INVENTORY_PLANNER, AUDITOR |
| GET | `/v1/stores/{store_id}` | One store, with its location when set | ADMIN, ANALYST, STORE_MANAGER, MARKETING, INVENTORY_PLANNER, AUDITOR |
| PUT | `/v1/stores/{store_id}/location` | Set latitude, longitude and geofence radius | ADMIN |
| DELETE | `/v1/stores/{store_id}/location` | Remove the location | ADMIN |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `store` | Table (`public`) | R: SELECT | Only the web writes the directory (E-10) |
| `stores.store_location` | Table (`stores`) | S: SELECT, INSERT, UPDATE, DELETE | New; 30 seed rows, one per seeded store; audited under entity `store_location` ([ADR-0046](../adr/0046-service-owned-data-lives-in-service-schemas.md)) |
| `stores_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_stores`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_stores.store_directory_v1` | check-ins, engagement |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:stores.v1.changed` | captured from `store` and `stores.store_location` | Own cache |
| Consumes | `stream:stores.v1.changed` | group `stores` | Invalidates its own directory cache |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| cache | `stores:cache:*` | 15 min | Directory reads, invalidated on change |
| state | `stores:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 422 | `invalid_body` | A CHECK on latitude, longitude or radius fails; `details` name the field |
| 404 | Undecided; the contract story fixes the code | An unknown `store_id` (F14-09 fixes the code) |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

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
| `redis-cache` down | Reads fall through to PostgreSQL; same answer, slower |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| `redis-state` full or down | The relay cannot `XADD`; unrelayed outbox rows wait until there is room, and consumers' caches expire by TTL |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0046](../adr/0046-service-owned-data-lives-in-service-schemas.md) Compliance (schema-qualified 30-row seed, no `svc_*` privilege on a `public` table outside the 27 legacy tables).
- [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) Compliance (`store` readable by `svc_stores` only; no DELETE on `store`).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-09 contract, F14-10 implementation; consumed by F17-03, F16-07. Issue numbers live in [`docs/backlog.md`](../backlog.md).

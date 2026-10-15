# audit-trail

> **Status:** Planned (phase 14, second wave). ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** The audit trail is append-only, attributed, free of credentials, and readable by ADMIN and AUDITOR only (RN-28, RN-29, RN-30)

## Summary

| Field | Value |
|---|---|
| Language | Rust (axum) |
| Port (loopback) | 5015 |
| Public path | `/api/audit-trail/v1/` |
| Wave | second wave |
| PostgreSQL role | `svc_audit_trail` |
| Redis ACL user | `svc_audit_trail`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-audit-trail` / `ghcr.io/equipo-03-proyecto10/mosaiq-audit-trail` |
| Used by | Electron: audit trail (second wave, F16-09). |
| Governing ADRs | [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md), [ADR-0046](../adr/0046-service-owned-data-lives-in-service-schemas.md) |

## Business rule

- Reads `audit_log` and nothing else. The table is written only by `fn_audit`, which runs as `definer_audit`, so no service role can write, alter or delete a row. The service strips `password_hash` again as defence in depth.
- Read-rule service (E-08). Because it consumes no stream and publishes nothing, it can be dropped without effect.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/audit-entries` | Query (`entity`, `entity_pk`, `user_id`, `action`, `from`, `to`, `page`, `size`) | ADMIN, AUDITOR |
| GET | `/v1/audit-entries/{audit_id}` | One entry | ADMIN, AUDITOR |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `audit_log` | Table (`public`) | R: SELECT; only `svc_audit_trail` may read it | Written by `fn_audit` for every writer (E-12) |

## Database contracts

None.

## Events

None.

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `audit_trail:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 404 | Undecided; the contract story fixes the code | An unknown `audit_id` (F14-29 fixes the code) |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-08 — read-rule services count as one-rule services

- **What:** Four services write no invariant of their own. They enforce a read rule: what they may report, and to whom.
- **Why it is acceptable:** Counting them follows the instructor's wording (one business rule per service), and the domain does not offer more writers: segmentation runs belong to the web (RN-05), and the audit log is written by `fn_audit`.
- **Guard:** The instructor confirmed that read-only boundaries count (Q-13, 2026-10-15). Even under the strict reading the floor would hold: ten core writers plus campaigns. ADR-0043 forbids cutting any core service.
- **Decided in:** [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (What counts, and the margin if Q-13 goes against it). Register: [E-08](./README.md#e-08--read-rule-services-count-as-one-rule-services).

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
| Second-wave service | If memory runs short it is dropped first (ADR-0043); audit rows keep being written by `fn_audit` |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) Compliance (only `svc_audit_trail` reads `audit_log`; no `svc_*` role writes it).
- [ADR-0046](../adr/0046-service-owned-data-lives-in-service-schemas.md) Compliance (an audited table's name is unique across schemas).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-29 contract, F14-30 implementation; consumed by F16-09. Issue numbers live in [`docs/backlog.md`](../backlog.md).

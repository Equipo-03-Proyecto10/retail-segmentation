# segments

> **Status:** Implemented (phase 14, core; F14-20, #461). ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** A customer has at most one current segment, named by its stable label code, and unassigned states are reported as they are (RN-20, RN-21)

## Summary

| Field | Value |
|---|---|
| Language | Rust (axum) |
| Port (loopback) | 5010 |
| Public path | `/api/segments/v1/` |
| Wave | core |
| PostgreSQL role | `svc_segments` |
| Redis ACL user | `svc_segments`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-segments` / `ghcr.io/equipo-03-proyecto10/mosaiq-segments` |
| Used by | Android: the current assignment (F17-05). Electron: segments and assignment history (F16-03). |
| Governing ADRs | [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md), [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) |

## Business rule

- Reads the segmentation tables as their declared read owner (E-10); the web's administrator run (RN-05) is their only writer. A segment is identified by its stable label code, never by a run-scoped id.
- A customer with no open assignment, or whose latest result is unassigned, is reported as exactly that (`no_open_assignment`, `unassigned_result`) and never filled in from elsewhere. Read-rule service (E-08).

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| GET | `/v1/segments` | Label vocabulary in business order, with sizes from the latest run | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| GET | `/v1/segments/{label_code}` | One label | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| GET | `/v1/segment-assignments/current?customer_id=` | Label, RFM scores, `since` and `run_id`, or the unassigned state | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |
| GET | `/v1/segment-assignments` | One customer's history, or one label's members (`customer_id` or `label_code`, `open`, `page`, `size`) | ADMIN, ANALYST, STORE_MANAGER, MARKETING, AUDITOR |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `segment_label`, `segment`, `segment_rule`, `segmentation_run`, `customer_segment_history` | Tables (`public`) | R: SELECT | Only the web's administrator run writes them (E-10) |
| `segments_events.outbox` | Table (outbox) | SELECT, UPDATE of `relayed_at`, DELETE (7-day purge) | Filled by `definer_segments`; seed-exempt runtime state |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Publishes | `api_segments.current_label_v1` | recommendations |
| Consumes | `api_customers.customer_ref_v1` | customers (a 404 for an unknown customer) |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Produces | `stream:segments.v1.run_recorded` | captured from `segmentation_run` INSERT | Own cache and recommendations' cache |
| Consumes | `stream:segments.v1.run_recorded` | group `segments` | Flushes run-scoped cache keys |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| cache | `segments:cache:{run_id}:*` | run-scoped | Segment reads; a new run changes the keys |
| state | `segments:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 404 | `customer_not_found` | The customer is absent from `api_customers.customer_ref_v1`. The unassigned states are 200 answers, not errors |
| 404 | `segment_not_found` | The `label_code` names no label |
| 422 | `invalid_query` | `GET /v1/segment-assignments` is not given exactly one of `customer_id` and `label_code`, or `.../current` has no valid `customer_id` |

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

- [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) Compliance (read access to the five tables for `svc_segments` only).
- [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) Compliance (registry: one rule, RN ids resolve).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-19 contract, F14-20 implementation; consumed by F17-05, F16-03. Issue numbers live in [`docs/backlog.md`](../backlog.md).

# check-ins

> **Status:** Planned (phase 14, core). ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** A staff check-in is verified only when the server-computed distance lies within the store's geofence at acceptable accuracy, and overrides are append-only reviews (RN-56)

## Summary

| Field | Value |
|---|---|
| Language | Rust (axum) |
| Port (loopback) | 5013 |
| Public path | `/api/check-ins/v1/` |
| Wave | core |
| PostgreSQL role | `svc_checkins` |
| Redis ACL user | `svc_checkins`, same user line on `redis-state` and `redis-cache` |
| MongoDB | — |
| Compose project / image | `mosaiq-check-ins` / `ghcr.io/equipo-03-proyecto10/mosaiq-check-ins` |
| Used by | Android: GPS check-in (F17-03). Electron: review (F16-04). |
| Governing ADRs | [ADR-0046](../adr/0046-service-owned-data-lives-in-service-schemas.md), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) |

## Business rule

- The server computes the distance from the reported fix to the store's location (`api_stores.store_directory_v1`) and records a `reason`: `within_geofence`, `outside_geofence`, `low_accuracy`, `store_location_unknown` or `no_fix`. A check-in is auto-verified exactly when its reason is `within_geofence`; a later review's verdict overrides it. `verified` is derived on read and never stored: the latest review's verdict when a review exists, else `auto_verified`.
- The reason is decided by the first matching case, with the distance computed as the haversine distance on a sphere of radius 6,371,008.8 m from the fix to the store's location, in metres with one decimal: `no_fix` (no fix sent), `store_location_unknown` (the store has no location), `low_accuracy` (`accuracy_m` greater than the store's `geofence_radius_m`), `within_geofence` (distance at most `geofence_radius_m`), otherwise `outside_geofence`. Each is a 201 answer, not an error.
- An override is a row in `checkins.check_in_review` (verified, a required reason, the reviewer). Reviews are append-only; a check-in is never updated or deleted.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| POST | `/v1/check-ins` | Check in (`store_id`, latitude, longitude, `accuracy_m`, `client_check_in_id`, `occurred_at`); returns 201 with `verified` and `reason` | ADMIN, STORE_MANAGER |
| GET | `/v1/check-ins` | List (`store_id`, `user_id`, `verified`, `from`, `to`, `page`, `size`) | ADMIN, STORE_MANAGER, AUDITOR |
| GET | `/v1/check-ins/{check_in_id}` | One check-in, with its reviews | ADMIN, STORE_MANAGER, AUDITOR |
| POST | `/v1/check-ins/{check_in_id}/reviews` | Append a manual review | ADMIN |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `checkins.store_check_in` | Table (`checkins`) | S: SELECT, INSERT | New; 30 seed rows; `client_check_in_id` unique; FKs to `store` and `app_user` ON DELETE RESTRICT (E-11) |
| `checkins.check_in_review` | Table (`checkins`) | S: SELECT, INSERT (append-only) | New; 30 seed rows; FKs to the check-in and `app_user` |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Consumes | `api_stores.store_directory_v1` | stores |

## Events

None.

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `checkins:rl:*` | the limit's window | Per-service request rate limit |
| state | `pub:auth:v1:live:{sid}` | — | `EXISTS` only, to check that the caller's session is live |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 200 | n/a (not an error) | A replayed `client_check_in_id` with the same user, store, fix and `occurred_at`: the original check-in, nothing written |
| 409 | `client_check_in_conflict` | The same `client_check_in_id` with any other value |
| 422 | `unknown_store` | The store is missing or inactive in `api_stores.store_directory_v1` |
| 422 | `occurred_in_future` | `occurred_at` is more than five minutes ahead of the server |
| 422 | `invalid_body` | Among others, a partial fix: `latitude`, `longitude` and `accuracy_m` are all present or all absent |
| 404 | `check_in_not_found` | An unknown `{check_in_id}` on a read or a review |
| 422 | `invalid_query` | `to` before `from`, with `details` naming the parameter |
| 503 | `database_unavailable` | Every `/v1` operation reads or writes PostgreSQL; the phone retries a refused check-in under the same `client_check_in_id` |

The five reasons are 201 answers, not errors.

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

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
| A store without a location | The check-in is recorded with reason `store_location_unknown`, unverified; it is not refused |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0046](../adr/0046-service-owned-data-lives-in-service-schemas.md) Compliance (schema-qualified seeds; reviews append-only).
- [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) Compliance (check-ins readable by `svc_checkins` only).
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-25 contract, F14-26 implementation; consumed by F17-03, F16-04. Issue numbers live in [`docs/backlog.md`](../backlog.md).

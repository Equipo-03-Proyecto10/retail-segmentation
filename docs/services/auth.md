# auth

> **Status:** Planned (phase 14, core). ADR-0034 to ADR-0046 accepted on 2026-10-15 (F13-01, #408).
> **One rule:** A principal is authenticated only by a valid credential of an active account, and every token stays usable only while its session is live (RN-52; related RN-03)

## Summary

| Field | Value |
|---|---|
| Language | Flask |
| Port (loopback) | 5001 |
| Public path | `/api/auth/v1/` |
| Wave | core |
| PostgreSQL role | `svc_auth` (no table of its own) |
| Redis ACL user | `svc_auth`, same user line on `redis-state` and `redis-cache`; it alone also holds `+multi +exec +discard +watch +unwatch`, for WATCH/MULTI/EXEC refresh rotation, besides `+waitaof` |
| MongoDB | — |
| Compose project / image | `mosaiq-auth` / `ghcr.io/equipo-03-proyecto10/mosaiq-auth` |
| Used by | Android: sign-in, refresh and sign-out (F17-01). Electron: sign-in and session handling (F16-01). |
| Governing ADRs | [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md), [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) |

## Business rule

- Verifies the argon2id hash read through `api_users.credential_v1`; an unknown email costs one dummy verify, so timing does not reveal accounts.
- Issues RS256 role-only access tokens (10 min) and rotating refresh tokens inside a session of at most 12 hours. A session is live exactly while `pub:auth:v1:live:{sid}` exists in `redis-state`.
- Revokes on sign-out, refresh-token reuse and every access change (deactivation, role change, administrator transfer, password change or reset, delete), through `stream:users.v1.access_changed` and a 60 s reconcile.
- Enforced in service code and Redis. auth owns no PostgreSQL table.

## Endpoints

| Method | Path | Purpose | Roles (`x-mosaiq-roles`) |
|---|---|---|---|
| POST | `/v1/sessions` | Sign in (email, password, device label); returns 201 with an access and a refresh token | No token |
| POST | `/v1/sessions/refresh` | Rotate the refresh token; re-reads the credential view | No token |
| GET | `/v1/sessions/current` | Session claims only (`sub`, `sid`, `role`, issued, expires) | Every authenticated role |
| DELETE | `/v1/sessions/current` | Sign out: deletes the liveness key, the session hash and the refresh family | Every authenticated role |
| GET | `/v1/jwks.json` | Public key set for verifiers outside the stack (JSON only, E-04) | No token |

Roles follow the [service operation policy](../requirements.md#service-operation-policy-distributed-stage). Meta routes (no token): `GET /health/live`, `GET /health/ready`, `GET /openapi.yaml`, `GET /schema.xsd`.

## Data it owns

S = only service writer, W = the frozen web also writes, R = read-only through its declared read owner.

| Object | Kind | Access | Notes |
|---|---|---|---|
| `auth:*` on `redis-state` | Redis | Private to auth | Session hash, user to sid set, refresh family, login counters |
| `pub:auth:v1:live:{sid}` | Redis | auth writes, every service `EXISTS` | The published liveness family |
| PostgreSQL | — | No table | Reads `api_users.credential_v1` only |

## Database contracts

| Direction | Object | Counterpart |
|---|---|---|
| Consumes | `api_users.credential_v1` | users (producer; auth is its only consumer) |

## Events

| Direction | Stream | Captured from / consumer group | Effect |
|---|---|---|---|
| Consumes | `stream:users.v1.access_changed` | group `auth` | Ends every session of that user |

## Redis keys

| Instance | Key pattern | TTL | Purpose |
|---|---|---|---|
| state | `auth:session:{sid}` | remaining absolute lifetime (≤ 12 h) | Session hash: user, role, expiry, password fingerprint |
| state | `auth:user_sids:{user_id}` | remaining absolute lifetime of the newest session | All sessions of a user, for revocation |
| state | `auth:refresh:{sha256}` | remaining absolute lifetime | One refresh token: `current` or `rotated` |
| state | `auth:refresh_family:{sid}` | remaining absolute lifetime | Refresh family: SET of the session's refresh-token SHA-256 values, so revocation deletes them without `SCAN` |
| state | `auth:rl:login:{ip}`, `auth:fail:{sha256(email)}:{ip}` | limit window (15 min for failures) | Sign-in limits; 5 failures lock (email, IP) for 15 min |
| state | `pub:auth:v1:live:{sid}` | remaining absolute lifetime, never extended | Liveness; the only family published to others |
| state | `auth:rl:*` | the limit's window | Per-service request rate limit |

## Service-specific errors

The shared envelope and mechanism codes are in [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md).

| HTTP | Code | When |
|---|---|---|
| 401 | `invalid_credentials` | Wrong password, unknown email or inactive account (indistinguishable) |
| 401 | `invalid_refresh` | The refresh token is unknown; no session is touched |
| 401 | `refresh_reused` | A rotated refresh token was presented again; the session is deleted |
| 401 | `session_expired` | A refresh after the 12 h absolute end |
| 401 | `session_revoked` | On refresh, the account is missing or inactive, the role differs or the password hash differs; the session is deleted |

## Accepted exceptions

Each is registered, with its justification and guard, in [`README.md`](./README.md#accepted-exceptions-register).

### E-04 — the public key is served as JSON only

- **What:** `GET /v1/jwks.json` answers only `application/jwk-set+json`. Every other operation answers JSON and XML.
- **Why it is acceptable:** RFC 7517 defines no XML form of a JWK Set. No client calls it and services read the mounted file, so it serves verifiers outside the stack.
- **Guard:** It is the single JSON-only operation. It needs no token, and an `Accept` that excludes JSON gets 406.
- **Decided in:** [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (Formats); [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md) (Keys). Register: [E-04](./README.md#e-04--the-public-key-is-served-as-json-only).

### E-15 — the credential view carries the password hash

- **What:** `api_users.credential_v1` exposes `password_hash` to auth, a different service from the one that writes it.
- **Why it is acceptable:** auth must verify argon2id hashes to authenticate, and the hash lives in `app_user`, owned by users. A copy would be a second source of credentials.
- **Guard:** It is the only view that carries a credential, and auth is its only consumer, checked by `grants.csv`. The hash never appears in a payload, an event or a log.
- **Decided in:** [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) (Reads: the published database contracts); [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md) (Sign-in and passwords). Register: [E-15](./README.md#e-15--the-credential-view-carries-the-password-hash).

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
| PostgreSQL unreachable | Sign-in and refresh read the credential view, so they answer 503 `database_unavailable`, never 500. Verification of existing tokens, `GET` and `DELETE /v1/sessions/current` and the JWKS do not use PostgreSQL |
| auth-service down | Existing sessions and every other service keep working (tokens verify locally). No sign-in, refresh or sign-out. Revocations wait; the bound is one access-token lifetime, 10 min |
| `redis-state` unreachable or over 200 ms | Every authenticated request returns 503 `auth_unavailable`; authentication fails closed and never falls back to the signature alone |
| Any other service down | No effect: services never call each other over HTTP |

## Compliance

- [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md) Compliance (token, liveness and revocation checks; `alg: none` and HS256 refused).
- [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) Compliance (ACL `DRYRUN` on `auth:*`, the liveness family and `MULTI`).
- Conformance suite: 401 without a token, 401 after sign-out (revoked `sid`), 503 with `redis-state` stopped.
- Registry and grant checks: [ADR-0043](../adr/0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (registry), [ADR-0035](../adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md) (resources are unique), [ADR-0034](../adr/0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (grant matrix).

## Backlog

F14-01 contract, F14-02 implementation; consumed by F17-01, F16-01. Issue numbers live in [`docs/backlog.md`](../backlog.md).

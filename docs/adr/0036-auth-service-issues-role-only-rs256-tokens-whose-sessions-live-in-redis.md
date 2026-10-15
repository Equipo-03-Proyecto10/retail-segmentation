# ADR-0036 — auth-service issues role-only RS256 tokens whose sessions live in Redis

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The distributed stage requires a JWT shared across components, with revocation through Redis: demonstration steps 2 and 3
are "a service validates the JWT" and "Redis confirms the session is live". Fifteen services take part ([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md));
the frozen web does not, and keeps `app_session` ([ADR-0022](0022-sessions-are-resolved-server-side-on-every-request.md), [ADR-0032](0032-server-side-sessions-have-idle-and-absolute-lifetimes.md)).
auth's rule is RN-52 (related RN-03). Four forces shape this record:
- **Who can mint.** HS256 with one shared secret lets every verifier forge any token, `ADMIN` included.
- **What the token says.** A `permissions` claim ties services to the web's vocabulary ([ADR-0045](0045-services-never-import-the-web-system.md)); the role code is immutable and canonical (RN-01, RN-02a).
- **Revocation.** Access changes are written by users-service *and* by the frozen web, which cannot call anything.
- **argon2.** The web's `argon2-cffi` defaults cost 64 MiB per verify, a real share of an 8 GB instance.

The floor role is decided in [ADR-0041](0041-android-and-electron-clients-run-different-processes-over-different-formats.md); the remaining judgement call is the revocation window while auth is down.

## Decision

auth-service is the only issuer. It signs short-lived RS256 access tokens that carry the caller's role code and no permissions. A
session lasts at most 12 hours from sign-in, and is live exactly while auth keeps `pub:auth:v1:live:{sid}` in `redis-state`. Every other
service verifies tokens with a mounted public key, checks that key on each request, fails closed when it cannot, and decides authority
from its own per-operation role policy. Revocation covers sign-out, refresh-token reuse and every access change, password changes
included, whatever wrote it, through `stream:users.v1.access_changed` and a 60-second reconcile. users-service enforces the
single-administrator rule twice, in its own code and through the existing partial unique index.

### Tokens

| Item | Value |
|---|---|
| Algorithm | RS256. The header carries `kid` |
| Claims (v1) | `iss`, `aud` = `mosaiq-api`, `sub` (`app_user.user_id`), `sid`, `role` (the immutable `role.code`), `jti`, `iat`, `exp`. **No `permissions` claim**, and no name or email |
| Access token | 10 minutes, and never past the session's absolute end |
| Session | Absolute lifetime of 12 hours from sign-in (one shift), held as `expires_at` in `auth:session:{sid}`. Refresh never extends it; a refresh after it gets 401 `session_expired` |
| Refresh token | Opaque 256-bit value that rotates on every use and never outlives its session. Stored only as `auth:refresh:{sha256}`, a HASH {`sid`, `state`: `current`\|`rotated`}, and indexed in `auth:refresh_family:{sid}`, a SET of those SHA-256 values, to which sign-in adds the first. Rotation `WATCH`es the old entry, then in one `MULTI`/`EXEC` marks it rotated, writes the new one and adds the new hash to the set; a concurrent rotation aborts the `EXEC`, and that request then finds the entry rotated. The refresh family is every `auth:refresh:{h}` for h in that set, plus the set. A rotated token presented again deletes the session: 401 `refresh_reused`. An unknown token gets 401 `invalid_refresh` and touches no session |
| Refresh | Re-reads `api_users.credential_v1` first, and rotates the refresh token only after that read and its checks pass. It refuses with 401 `session_revoked`, deleting the session, if the account is missing or inactive, the role differs, or the password hash differs from the session's SHA-256 fingerprint. If PostgreSQL fails, it answers 503 `database_unavailable` and leaves the presented refresh entry `current`, so a retry with the same token succeeds once PostgreSQL is back and never trips the reuse detection. The new access token always carries the session's role |
| Transport | `Authorization: Bearer` only. The web neither issues nor accepts these tokens |

The claims contract is `contracts/auth/` (v1): adding a claim is non-breaking, removing or renaming one is a v2.

### Keys

- The private key is mounted read-only in auth only, at the paths in [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md); the JWKS is at `/run/mosaiq/jwks.json` in every service.
- The kit reloads the JWKS on `SIGHUP` or an unknown `kid`, at most once every 30 s.
- `GET /v1/jwks.json` serves verifiers outside the stack only; it is JSON-only ([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)), and services never fetch it.
- Rotation publishes the next `kid`, sends `SIGHUP`, switches the signer and drops the old `kid` after 10 minutes plus skew; refresh tokens are unaffected. The runbook lives in `deploy/`.

### Verification in every service

The mechanism lives in the pinned kit ([ADR-0045](0045-services-never-import-the-web-system.md)); no service queries PostgreSQL to authenticate:
1. Pin RS256 (PyJWT `algorithms=["RS256"]`; `Validation::new(Algorithm::RS256)`). `alg: none` and HS256 are refused first.
2. Check the signature against the `kid`, then `iss`, `aud` and `exp`. Any failure is 401 `invalid_token`.
3. `EXISTS pub:auth:v1:live:{sid}` on `redis-state`, 200 ms timeout. Missing is 401 `session_revoked`; unreachable or slow is **503 `auth_unavailable`**, never a signature-only fallback.
4. Check `role` against the operation's `x-mosaiq-roles`; outside it is 403 `forbidden_role`.

The liveness key is a STRING whose TTL is the session's remaining absolute lifetime, set at sign-in and never extended; `EXISTS` means live (`contracts/auth/redis-liveness.v1.md`).

### Sessions and revocation

auth's state lives under `auth:*` on `redis-state`, as listed in [ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)'s key table.
A revoked token is one whose `pub:auth:v1:live:{sid}` no longer exists; that is how Redis holds revoked tokens, and no `jti` is recorded.

| Event | Who changes it | How auth learns | Effect |
|---|---|---|---|
| Sign-out (`DELETE /v1/sessions/current`) | auth | Directly | Deletes the liveness key, the session hash and the refresh family |
| Refresh-token reuse | auth | Directly | Same as sign-out |
| Deactivation, role change, administrator transfer, password change or reset, hard delete | users-service or the web | the `app_user` capture and `stream:users.v1.access_changed` ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)) | Same as sign-out, for every sid in `auth:user_sids:{user_id}`; the user signs in again |
| Every 60 s, auth checks each live session against `api_users.credential_v1` as refresh does | — | The reconcile | Same as sign-out for each session that fails; a pass that cannot read PostgreSQL deletes nothing and runs again on the next tick |

After deleting, auth runs `WAITAOF 1 0 1000` before it answers or XACKs; if that fails, sign-out answers 503 `auth_unavailable` and the entry stays pending.
Bounds: normally the relay's latency (5 s poll); 60 s when the stream is down; one access-token lifetime, 10 minutes, when auth is down.
The web's own sessions are untouched: `load_principal` re-reads `is_active` and the role on every request, so a deactivation made through users-service ends them on their next request.

### Sign-in and passwords

- `POST /v1/sessions` verifies the argon2id hash from `api_users.credential_v1`. auth is the view's only consumer; users, its producer, also holds SELECT ([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)). Unknown emails cost one dummy verify.
- The client IP comes only from `X-Real-IP`, which NGINX overwrites ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)). A per-IP limit applies, and 5 failures lock
  (email, IP) for 15 minutes, so nobody can lock the single `ADMIN` out from elsewhere. NGINX `limit_req` applies to sign-in.
- Sizing: auth verifies 2 at a time in 384 MB; users-service hashes 1 at a time in 224 MB. auth writes no table.
- users-service's current-password check allows 5 failures per 15 minutes per user (`users:rl:pwd:{user_id}`, then 429). A password change or reset ends every session of that user, the caller's own included.

### Authorization is local to each service

The token carries only the role code. Each service enforces its own per-operation `x-mosaiq-roles`, transcribed from [Service operation policy (distributed stage)](../requirements.md#service-operation-policy-distributed-stage).
That policy records the `STORE_MANAGER` floor writes and the `/v1/users/me` and own-password operations open to every authenticated role, `CUSTOMER` included.
The web's §3 matrix is unchanged; `web/middleware/authz.py` and `tests/test_authz.py` are imported by nothing, and the kit supplies the check but no role names.

### The single administrator, enforced twice in users-service

- **Its own check:** `POST /v1/users` with `ADMIN` is 409 `single_administrator`; a `PATCH` that would promote to or demote from `ADMIN` is 409 `use_administrator_transfer`; deactivating the only `ADMIN` is 409 `administrator_required`.
- **The transfer:** `POST /v1/administrator-transfers` locks the current `ADMIN` row and the successor row `FOR UPDATE` in one transaction, demotes, then promotes. The successor must exist, be active and not be the incumbent (409 `successor_inactive`, 422 `invalid_successor`).
- **The index:** a 23505 from `ux_app_user_single_administrator` maps to 409 `single_administrator`, never 500. *Never zero* is application-only in both writers (RN-01).

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| HS256 with one shared secret, as in the instructor's reference | Every verifier could forge any token, including an `ADMIN` one. RS256 costs one key pair and a mounted file |
| A `permissions` claim, with names from `web/middleware/authz.py` | It couples every service to the frozen web's vocabulary ([ADR-0045](0045-services-never-import-the-web-system.md)), makes auth the owner of every policy, and freezes a policy snapshot into each token |
| Services fetch `GET /v1/jwks.json` over HTTP | Service-to-service HTTP, which the stage forbids, and every cold start would depend on auth |
| Services read `auth:session:{sid}` directly | It exposes auth's private hash as an unwritten contract. A published STRING checked with `EXISTS` is enforceable by one ACL selector |
| A denylist of revoked `jti`s | It cannot say "this session is live", and revoking a user would need every outstanding `jti` |
| Revocation by users-service calls, or a `users` `pg_notify` that auth LISTENs to | The web cannot call; `pg_notify` is lost while disconnected and crosses a boundary ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)). The outbox stream is durable and captures the web's writes |
| Fail open when `redis-state` is unreachable | Revoked users would regain access during exactly the outage an attacker can cause |
| Single administrator by the index alone, or by the check alone | `AGENTS.md` counts both or neither. The index alone is an unexplained error; the check alone is bypassed by a concurrent web write |

## Consequences

**What this makes easy.**
- A compromised non-auth service cannot mint a token. Revocation reaches every service on its next request, whichever writer changed the account.
- A service changes its own policy in its own pull request, with no reissued tokens.
- Keys rotate with no rebuild, and both clients use one flow: Bearer, and refresh on 401.

**What this makes hard.**
- **`redis-state` is on every request's critical path.** When it is down, every authenticated route answers 503.
- **Fifteen copies of the policy.** Only review and the conformance test hold them to the policy subsection.
- **No store scoping.** `app_user` has no store (ADR-0010), so a store manager reads every store's sales and check-ins.
- **The floor writes widen §3 for the services only.** A store manager records sales and registers customers; the web still refuses both.
- **Sessions end after 12 hours.** Staff sign in again every shift, whatever their activity, and a stolen refresh token is worth at most what is left of that session.
- **A password change ends every session of that user,** the caller's own included.
- **auth down means a 10-minute window.** A user deactivated meanwhile keeps a valid access token until it expires.
- **Key rotation is a runbook,** not automation; services reject new tokens until they reload the file.
- **Single-flight refresh.** Concurrent refreshes trip the reuse detection and sign the user out.
- **argon2 memory.** Sign-in throughput is two at a time: enough for a demonstration, a ceiling for anything else.

**What must now be true elsewhere.**
- [Service operation policy (distributed stage)](../requirements.md#service-operation-policy-distributed-stage) holds the policy; the web's §3 matrix is unchanged.
- [ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) reserves `auth:*` for auth, gives it `+waitaof` and `+multi +exec +discard +watch +unwatch`, and gives no service `@scripting`; [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) defines the `app_user` capture (`password_hash` included) and the stream.
- `sql/01_schema.sql` defines `api_users.credential_v1`; auth is its only consumer and users also holds SELECT. No `svc_*` role touches `app_session`.
- [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md) mounts `/etc/mosaiq/keys/auth-<kid>.pem` in auth only and `/run/mosaiq/jwks.json` in every service, and holds the NGINX `X-Real-IP` and `limit_req` settings.
- [ADR-0045](0045-services-never-import-the-web-system.md): `kit-py` and `kit-rs` implement verification identically. [ADR-0041](0041-android-and-electron-clients-run-different-processes-over-different-formats.md): both clients refresh single-flight, and Electron keeps tokens in `safeStorage`.
- `.env.example` documents `AUTH_ISSUER`, `AUTH_PRIVATE_KEY_PATH`, `AUTH_JWKS_PATH`, `AUTH_ACCESS_TOKEN_TTL_SECONDS=600` and `AUTH_SESSION_ABSOLUTE_TTL_SECONDS=43200`, with no key material.

## Compliance

```bash
# Each prints nothing: only auth signs; no shared secret; no permissions claim; RS256 pinned; no password hash in a contract.
grep -rnE 'jwt\.encode|EncodingKey::from' services/ packages/ bench/ | grep -v '^services/auth/'
grep -rnE 'HS256|HS384|HS512|JWT_SECRET' services/ packages/ bench/ contracts/ .env.example
grep -rnE '"permissions"|\bpermissions\s*:' contracts/auth/ services/auth/; grep -rn password_hash contracts/users/
grep -rnE 'algorithms\s*=\s*\[' packages/kit-py | grep -v '"RS256"\]'; grep -rn 'Validation::new(' packages/kit-rs | grep -v 'Algorithm::RS256'
for f in contracts/*/openapi.yaml; do   # every token-bearing operation declares x-mosaiq-roles
  yq -r '.paths | to_entries[] | .key as $p | .value | to_entries[] | select(.key | test("^(get|post|put|patch|delete)$"))
         | select(.value."x-mosaiq-roles" == null and (.value.security // [""]) != []) | "\($p) \(.key)"' "$f" | sed "s|^|$f: |"
done
```

```sql
-- No service touches the web's sessions. Only auth (and users, its producer) reads the credential view. Returns no row.
SELECT r.rolname FROM pg_roles r WHERE r.rolname LIKE 'svc\_%' AND (has_table_privilege(r.rolname, 'public.app_session', 'SELECT,INSERT,UPDATE,DELETE')
        OR (r.rolname NOT IN ('svc_auth','svc_users') AND has_table_privilege(r.rolname, 'api_users.credential_v1', 'SELECT')));
SELECT indexdef FROM pg_indexes WHERE indexname = 'ux_app_user_single_administrator';  -- ... (role_id) WHERE (role_id = 1)
```

The black-box suite asserts, against every image: 401 for no token, `alg: none`, HS256 keyed with the public key, a wrong `aud`, an expired
token, a tampered payload and an unknown `kid`; 401 `session_revoked` after sign-out; 503 `auth_unavailable` with `redis-state` stopped; 403 `forbidden_role`.
auth's tests: reuse of a rotated refresh token deletes the session; two concurrent refreshes of the same current token produce exactly one success; with PostgreSQL stopped, a refresh gets 503 `database_unavailable` and the same token refreshes once it is back, and a reconcile pass deletes no session; a deactivation made as `retail_app` removes the liveness keys within 60 s
with the relay stopped; after an administrator transfer, the old `ADMIN`'s refresh returns 401; a refresh past 12 hours returns 401 `session_expired`.
users-service's tests: a second `ADMIN` gets 409 `single_administrator`, never 500, including one written concurrently through the web role; deactivating the only
`ADMIN` gets 409; a transfer to an inactive successor gets 409 `successor_inactive`; a transfer leaves exactly one `ADMIN`.

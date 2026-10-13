# ADR-0036 — auth-service issues role-only RS256 tokens whose sessions live in Redis

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The distributed stage requires a JWT shared across components, with revocation
through Redis. Steps 2 and 3 of the end-to-end demonstration are "a service
validates the JWT" and "Redis confirms the session is live". Fifteen services
in two languages take part
([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md)).
The web system does not. It is frozen, keeps its server-side sessions in
`app_session`
([ADR-0022](0022-sessions-are-resolved-server-side-on-every-request.md),
[ADR-0032](0032-server-side-sessions-have-idle-and-absolute-lifetimes.md)),
connects to no Redis and consumes no service.

Four forces shape this record.

**Who can mint a token.** The instructor's reference signs with HS256 and gives
the same secret to every service. Then every service that can verify a token
can also forge one, including an `ADMIN` token. With fifteen deployables, a leak
from any one of them would compromise all of them.

**What the token says about authority.** The earlier draft of this record put a
`permissions` claim in the token, with names taken from
`web/middleware/authz.py`. That ties every service to the web's permission
vocabulary, which [ADR-0045](0045-services-never-import-the-web-system.md) now
forbids. It also freezes policy into the token for its lifetime, and hands the
issuer a decision that belongs to the service doing the work. The role code
does not have these problems. It is already immutable (`trg_role_code_immutable`,
RN-01) and canonical (RN-02a), and seven codes cover every staff and customer
account.

**Revocation outside auth's own writes.** A signed token cannot be withdrawn
before it expires. Sign-out is auth's own act, but deactivation, role changes
and administrator transfers are made by users-service *and* by the frozen web,
which cannot be taught to call anything. The earlier draft listened for a
`users.deactivated` event that only users-service would emit, so it missed
every change made in the web.

**What argon2 costs.** The web hashes passwords with `argon2-cffi` 23.1.0
`PasswordHasher()` defaults (`web/services/auth.py`, pinned in
`web/requirements.txt`). Those defaults use 64 MiB of memory, time cost 3 and
parallelism 4. A verify must use the parameters stored in the hash, so every
sign-in allocates 64 MiB. That is a real share of a container on an 8 GB
instance that also runs PostgreSQL, the web and fourteen other services.

Two points were judgement calls. First, the role policy for the Android floor
role: `docs/requirements.md` §3 gives `STORE_MANAGER` no write today. The team
chose `STORE_MANAGER` as the floor writer, following the
[ADR-0023](0023-store-managers-read-segments-and-customers.md) precedent,
rather than adding an eighth role. Second, the length of the revocation window
when auth itself is down, which is stated below rather than hidden.

## Decision

auth-service is the only issuer. It signs short-lived RS256 access tokens that
carry the caller's role code and no permissions. Each session is live exactly
while auth keeps the published key `pub:auth:v1:live:{sid}` in `redis-state`.
Every other service verifies tokens with a mounted public key, checks that key
on each request, fails closed when it cannot, and decides authority from its
own per-operation role policy. Revocation reaches auth through
`stream:users.v1.access_changed`, whatever wrote the change, and a 60-second
reconcile backs it up. users-service enforces the single-administrator rule
twice, in its own code and through the existing partial unique index. The
tables below are the detail of that one decision.

### Tokens

| Item | Value |
|---|---|
| Algorithm | RS256. The header carries `kid` |
| Claims (v1) | `iss`, `aud` = `mosaiq-api`, `sub` (the `app_user.user_id`), `sid`, `role` (the immutable `role.code`), `jti`, `iat`, `exp`. **No `permissions` claim**, and no name or email |
| Access token | 10 minutes |
| Refresh token | Opaque 256-bit random value, 7 days. auth stores only its SHA-256 (`auth:refresh:{sha256}`). Every use rotates it. Presenting a used one is treated as theft: auth deletes the whole session and answers 401 `refresh_reused` |
| Refresh | Re-reads `api_users.credential_v1`. An inactive account gets 401 `account_inactive`, and its session is deleted |
| Transport | `Authorization: Bearer` only. The web neither issues nor accepts these tokens |

The claims contract is `contracts/auth/` (v1). Adding a claim is
non-breaking. Removing or renaming one is a v2.

### Keys

- The private key is a file mounted read-only in the auth container only. It is
  never committed, and never present in another container or image.
- The deploy writes the public keys to `/run/mosaiq/jwks.json`, which is mounted
  read-only in every service. The file holds the current `kid` and, during a
  rotation, the next one.
- The kit loads the file at start. It reloads on `SIGHUP`, and on a token with
  an unknown `kid`. Reloads on an unknown `kid` are rate-limited, so forged
  `kid`s cannot turn into a stream of file reads.
- `GET /v1/jwks.json` serves the same set for verifiers outside the stack.
  Services never fetch it over HTTP.
- **Rotation**, with overlap:
  1. Publish the next public key beside the current one, and `SIGHUP` every
     service.
  2. Switch auth to signing with the next key.
  3. Remove the old `kid` once one access-token lifetime (10 minutes) plus
     clock skew has passed.

  Refresh tokens are opaque and unaffected, so no client is signed out.
  Rotation needs no rebuild.

### Verification in every service

The mechanism lives in the pinned kit
([ADR-0045](0045-services-never-import-the-web-system.md)):

1. Pin the algorithm: PyJWT `algorithms=["RS256"]`; `jsonwebtoken`
   `Validation::new(Algorithm::RS256)`. `alg: none`, HS256 and every other
   algorithm are refused before the signature is looked at.
2. Check the signature against the `kid` in the mounted set, then `iss`, `aud`
   and `exp`. Any failure returns 401 `invalid_token`.
3. Run `EXISTS pub:auth:v1:live:{sid}` on `redis-state` with a 200 ms timeout.
   - A missing key returns 401 `session_revoked`.
   - An unreachable Redis or a timeout returns **503 `auth_unavailable`**.
     Authentication fails closed. It never falls back to the signature alone.
4. Check `role` against the operation's `x-mosaiq-roles`. A role outside it
   returns 403 `forbidden_role`.

No service queries PostgreSQL to authenticate a request. The liveness family is
the only part of auth's Redis state another service can see. Its ACL selector
is `%R~pub:auth:v1:live:* +exists`, and the session hash behind it stays private
under `auth:*`
([ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)).
The key contract is `contracts/auth/redis-liveness.v1.md`:
- the type is STRING;
- its TTL is set to the lifetime of the session's newest refresh token, on
  issue and on every rotation;
- `EXISTS` means live.

### Sessions and revocation

auth's private state lives under `auth:*` on `redis-state`:
- `auth:session:{sid}` (HASH);
- `auth:user_sids:{user_id}` (SET);
- `auth:refresh:{sha256}`;
- `auth:revoked:jti:{jti}`, holding the jti of an access token revoked by
  sign-out until that token's `exp`, and checked by auth's own endpoints;
- `auth:rl:login:{ip}` and `auth:fail:{sha256(email)}`.

| Event | Who changes it | How auth learns | Effect |
|---|---|---|---|
| Sign-out (`DELETE /v1/sessions/current`) | auth | Directly | Deletes the liveness key, the session hash and the refresh family. The next request anywhere gets 401 |
| Refresh-token reuse | auth | Directly | Same as sign-out |
| Deactivation, role change, administrator transfer, hard delete | users-service **or the web** | The `app_user` trigger writes users' outbox. The relay publishes `stream:users.v1.access_changed`, keys only ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)). auth's consumer group reads it | Deletes every liveness key in `auth:user_sids:{user_id}`. The user signs in again |
| Any of the above, if the stream path lags or fails | — | Every 60 s auth reconciles each live session against `api_users.credential_v1` (`is_active`, `role_code`) | Deletes every session whose account is inactive, missing, or holds a different role than its token |

The bounds are:
- **Normal path:** the relay's latency. The relay wakes on users' own
  `pg_notify`, or polls every 5 s.
- **Stream down:** 60 s, from the reconcile.
- **auth down:** one access-token lifetime, 10 minutes. Nothing deletes keys
  while auth is down, but no refresh succeeds either.

The web's own sessions are untouched. `load_principal` in `web/db/sessions.py`
re-reads `is_active` and the role on every request, so a deactivation made
through users-service ends web sessions on their next request. No service holds
any privilege on `app_session`.

### Sign-in

- `POST /v1/sessions` verifies the argon2id hash read from
  `api_users.credential_v1`. The view is granted to `svc_auth` only.
- Unknown emails cost one dummy verify, so timing does not reveal which
  accounts exist. This is auth's own code: it mirrors the web's `_DUMMY_HASH`
  and does not import it.
- Sign-in is rate-limited per IP. After five failures for one email, that
  email is locked for 15 minutes.
- **Sizing.** auth runs in 384 MB with a semaphore of two concurrent verifies
  (2 × 64 MiB). Sign-ins beyond that wait on the semaphore. users-service
  hashes on create and password reset with the same parameters, in 224 MB, with
  a semaphore of one.
- auth writes no table, so it never rehashes a password.

### Authorization is local to each service

The token carries only the role code. Each service owns its role-to-operation
policy as `x-mosaiq-roles` on every OpenAPI operation
([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)),
and enforces it in its own code. The kit supplies the check and no role names.
`web/middleware/authz.py` and `tests/test_authz.py` stay untouched and are
imported by nothing. The policy is transcribed from `docs/requirements.md` §3:

| Operations | `x-mosaiq-roles` | Basis in §3 |
|---|---|---|
| Reads of products, categories, stores, stock levels and channels | `ADMIN`, `ANALYST`, `STORE_MANAGER`, `MARKETING`, `INVENTORY_PLANNER`, `AUDITOR` | Catalogs: read. Stock follows the catalog ([ADR-0010](0010-the-consultation-module-is-a-separate-read-only-blueprint.md)) |
| Writes of products, categories and store locations | `ADMIN` | Catalogs: full for `ADMIN` only |
| Reads of customers, preference profiles, segments, segment assignments and recommendations | `ADMIN`, `ANALYST`, `STORE_MANAGER`, `MARKETING`, `AUDITOR` | Segments: read. Customers follow `segment.read` ([ADR-0010](0010-the-consultation-module-is-a-separate-read-only-blueprint.md), [ADR-0023](0023-store-managers-read-segments-and-customers.md)) |
| **Floor writes:** `POST /v1/sales`; `POST` and `PATCH /v1/customers`; `PUT /v1/preference-profiles/...`; `POST /v1/check-ins`; `POST /v1/engagement-events` | `ADMIN`, `STORE_MANAGER` | **New cells.** `STORE_MANAGER` is the floor writer, following ADR-0023. §3 must record them before this record is accepted |
| Reads of sales, engagement events, engagement summaries and check-ins | `ADMIN`, `ANALYST`, `STORE_MANAGER`, `MARKETING`, `INVENTORY_PLANNER`, `AUDITOR` | Reports: read |
| Check-in reviews and engagement voids | `ADMIN` | Administrative correction |
| `users`, `roles` reads | `ADMIN`, `AUDITOR` | Users: full for `ADMIN`, read for `AUDITOR` |
| `users` writes and `administrator-transfers` | `ADMIN` | Users: full |
| `GET /v1/users/me`; `PUT /v1/users/{own id}/password` with the current password | Any authenticated role | Users: own |
| `campaigns` reads | `ADMIN`, `ANALYST`, `MARKETING`, `AUDITOR` | Campaigns: read |
| `campaigns` writes and transitions | `ADMIN`, `MARKETING` | Campaigns: full |
| `audit-entries` | `ADMIN`, `AUDITOR` | Audit log: read |
| Sign-in, refresh, `jwks.json`, meta routes | No token | — |

`CUSTOMER` is refused everywhere except sign-in, refresh, sign-out and its own
`/v1/users/me` and password. `INVENTORY_PLANNER`'s `inventory` write stays
deferred (`docs/roadmap.md`), so no operation lists it.

### The single administrator, enforced twice in users-service

- **Its own check:**
  - `POST /v1/users` with role `ADMIN` returns 409 `single_administrator`.
  - A `PATCH` that would promote to or demote from `ADMIN` returns 409
    `use_administrator_transfer`.
  - Deactivating the only `ADMIN` returns 409 `administrator_required`.
  - The role moves only through `POST /v1/administrator-transfers`. In one
    transaction, it locks the current `ADMIN` row with `SELECT ... FOR UPDATE`,
    demotes it, then promotes the successor.
- **The schema half:** the partial unique index
  `ux_app_user_single_administrator` on `app_user (role_id) WHERE role_id = 1`.
  A 23505 naming that index is mapped to 409 `single_administrator`, never 500.

This matters more now than in the first delivery. The web keeps both of its own
halves in `web/services/users.py`, unchanged, so two application checks now
race against each other, and only the index sees both writers. *Never zero* is
still application-only in both writers, as RN-01 already records.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| HS256 with one shared secret, as in the instructor's reference | Every verifier could forge any token, including an `ADMIN` one, so a configuration leak from any of fifteen services compromises all of them. RS256 costs one key pair and a mounted file, and PyJWT and `jsonwebtoken` support it equally |
| A `permissions` claim, with names from `web/middleware/authz.py` (the earlier draft) | It couples every service to the frozen web's vocabulary, which [ADR-0045](0045-services-never-import-the-web-system.md) forbids. It makes auth the owner of every service's policy. And it freezes a policy snapshot into each token for its lifetime. The role code is immutable, so it is a stable key that each service can map itself |
| EdDSA (Ed25519) | Equally sound, with smaller keys. RS256 has wider support in the tooling and examples the team uses. Either would pass review |
| Services fetch `GET /v1/jwks.json` from auth over HTTP | That is a service-to-service HTTP call, which the stage forbids. It also makes every cold start depend on auth being up. A mounted file needs neither |
| Services read `auth:session:{sid}` directly (the earlier draft) | It exposes auth's private session hash, and its fields become a contract no one wrote down. A published STRING key checked with `EXISTS` is a contract one ACL selector can enforce |
| A denylist of revoked `jti`s instead of a liveness allowlist | A denylist cannot say "this session is live", which the demonstration checks. Revoking a user would need every outstanding `jti`. Deleting the `sid` keys revokes them all |
| Revocation from users-service calls, or from a `users` `pg_notify` channel that auth LISTENs to | A call would be service-to-service HTTP, and the web, which also deactivates users, cannot make one. `pg_notify` is lost while the listener is disconnected, and a service listening on another boundary's channel is forbidden ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)). The outbox stream is durable and also captures the web's writes |
| Fail open when `redis-state` is unreachable | Revoked and deactivated users would regain access during exactly the outage an attacker can cause. A 503 is the honest answer |
| Single administrator by the index alone in users-service, or by its own check alone | `AGENTS.md` counts both or neither. The index alone surfaces as an unexplained database error. The check alone is bypassed by a concurrent web write or a direct `INSERT` |
| The web adopts these tokens and Redis | The web is frozen, and the instructor confirmed only the services consult Redis (Q-12 in [`scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md)). ADR-0022 and ADR-0032 already give it revocable sessions |

## Consequences

**What this makes easy.**
- A compromised non-auth service cannot mint a token.
- Revocation reaches every service on its next request, whichever writer
  changed the account.
- Authenticating a request needs no PostgreSQL query.
- A service changes its own policy in its own pull request, with no reissued
  tokens and no change to auth.
- Keys rotate with no rebuild, and no client is signed out.
- Both clients use one flow: Bearer, and refresh on 401.

**What this makes hard.**
- **`redis-state` is on every request's critical path.** When it is down,
  every authenticated route in every service returns 503. It is a declared
  single point of failure.
- **Fifteen copies of the policy.** `x-mosaiq-roles` is transcribed per service,
  and nothing makes those copies agree with `docs/requirements.md` §3 except
  review and the conformance test below. A change to §3 is a change to several
  services.
- **No store scoping.** `STORE_MANAGER`'s "own store" reports cell is not
  narrowed. `app_user` has no store (ADR-0010), so a store manager reads sales
  and check-ins for every store, as in the web.
- **The `STORE_MANAGER` floor writes widen §3.** A store manager can now record
  sales and register customers. The web never allowed either, and the web
  still does not.
- **Sessions have no absolute lifetime here.** A refresh token used at least
  once a week keeps its session alive indefinitely, until sign-out, a
  revocation or the reconcile ends it. The web caps its own sessions at
  8 hours (ADR-0032). This record does not, and a cap would be a later change
  to the liveness TTL.
- **auth down means a 10-minute window.** A user deactivated while auth is
  down keeps a valid access token until it expires.
- **Key rotation is a runbook,** not automation. Switching the signer before
  every service has the next key signs nobody out, but services reject new
  tokens until they reload the file.
- **Single-flight refresh.** Clients must refresh one at a time, or concurrent
  401s race and trip the reuse detection, which signs the user out.
- **argon2 memory.** Sign-in throughput is two at a time. That is enough for a
  course demonstration, and a ceiling for anything else.

**What must now be true elsewhere.**
- `docs/requirements.md` §3 records the floor-write cells for `STORE_MANAGER`,
  and the `own` users cell as a live route for `/v1/users/me`.
- [ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)
  reserves `auth:*` for auth alone, and gives every other service only the
  liveness selector outside its own prefixes.
- [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)
  defines the `app_user` trigger, users' outbox and
  `stream:users.v1.access_changed`, with auth as its only consumer group.
- `sql/01_schema.sql` defines `api_users.credential_v1` (`user_id`,
  `email_lower`, `password_hash`, `is_active`, `role_code`), with SELECT for
  `svc_auth` only. No `svc_*` role holds any privilege on `app_session`.
- [ADR-0045](0045-services-never-import-the-web-system.md): `kit-py` and
  `kit-rs` implement the verification steps identically, and the shared
  black-box suite proves both.
- [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md):
  the deploy mounts `/run/mosaiq/jwks.json` in every service and the private
  key in auth only. The key-rotation runbook lives in `deploy/`.
- [ADR-0041](0041-android-and-electron-clients-run-different-processes-over-different-formats.md):
  both clients implement single-flight refresh, and Electron keeps tokens in
  `safeStorage`.
- `.env.example` documents the issuer, the key and JWKS paths, and the
  lifetimes, with safe defaults and no key material.

## Compliance

```bash
# Only auth signs. No shared-secret JWT anywhere. No permissions claim.
grep -rnE 'jwt\.encode|EncodingKey::from' services/ packages/ bench/ | grep -v '^services/auth/'
grep -rnE 'HS256|HS384|HS512|JWT_SECRET' services/ packages/ bench/ contracts/ .env.example
grep -rnE '"permissions"|\bpermissions\s*:' contracts/auth/ services/auth/
# All three must print nothing.

# Every verifier pins RS256 (the kits are the only verifiers).
grep -rnE 'algorithms\s*=\s*\[' packages/kit-py | grep -v '"RS256"\]'   # empty
grep -rn 'Validation::new(' packages/kit-rs | grep -v 'Algorithm::RS256' # empty

# Every OpenAPI operation declares x-mosaiq-roles, except the unauthenticated ones.
for f in contracts/*/openapi.yaml; do
  yq -r '.paths | to_entries[] | .key as $p | .value | to_entries[]
         | select(.key | test("^(get|post|put|patch|delete)$"))
         | select(.value."x-mosaiq-roles" == null and (.value.security // [""]) != [])
         | "\($p) \(.key)"' "$f" | sed "s|^|$f: |"
done   # prints nothing
```

```sql
-- No service can touch the web's sessions. Only svc_auth reads the credential view.
SELECT r.rolname FROM pg_roles r
 WHERE r.rolname LIKE 'svc\_%'
   AND (has_table_privilege(r.rolname, 'public.app_session', 'SELECT,INSERT,UPDATE,DELETE')
        OR (r.rolname <> 'svc_auth'
            AND has_table_privilege(r.rolname, 'api_users.credential_v1', 'SELECT')));
-- returns no row

-- The schema half of the single-administrator rule is still there.
SELECT indexdef FROM pg_indexes WHERE indexname = 'ux_app_user_single_administrator';
-- CREATE UNIQUE INDEX ... ON public.app_user USING btree (role_id) WHERE (role_id = 1)
```

```bash
# Redis isolation, for every non-auth service user (ADR-0037 runs this for all of them).
redis-cli -h redis-state ACL DRYRUN svc_sales EXISTS pub:auth:v1:live:x   # OK
redis-cli -h redis-state ACL DRYRUN svc_sales HGETALL auth:session:x      # denied
redis-cli -h redis-state ACL DRYRUN svc_sales DEL pub:auth:v1:live:x      # denied
```

The shared black-box suite runs against every service image, and asserts:
- 401 for: no token; `alg: none`; HS256 signed with the public key as the
  secret; a wrong `aud`; an expired token; a tampered payload; an unknown
  `kid`.
- 401 `session_revoked` on the request after sign-out.
- 503 `auth_unavailable` with `redis-state` stopped.
- 403 `forbidden_role` for a role outside `x-mosaiq-roles`.

auth's own tests assert that refresh-token reuse deletes the session. They also
assert that a deactivation made as `retail_app`, the web's role, removes that
user's liveness keys within the 60-second reconcile with the relay stopped, and
within seconds with it running.

users-service's tests assert:
- `POST /v1/users` with `ADMIN` returns 409 `single_administrator`, and so
  does a second `ADMIN` written concurrently through the web role. Neither
  returns 500.
- Deactivating the only `ADMIN` returns 409 `administrator_required`.
- A transfer leaves exactly one `ADMIN`.

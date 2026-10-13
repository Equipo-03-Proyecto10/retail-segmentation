# ADR-0036 — auth-service issues RS256 tokens that every service verifies and Redis can revoke

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The second delivery requires JWT "issued and validated across components, with
revocation enforced through Redis". The end-to-end demonstration specifically
requires that "Redis confirms that the session is live". Two clients and eight
services take part. The web system does not: it keeps its server-side sessions
in PostgreSQL ([ADR-0022](0022-sessions-are-resolved-server-side-on-every-request.md),
[ADR-0032](0032-server-side-sessions-have-idle-and-absolute-lifetimes.md)).
It connects to no Redis and consumes no service (#398).

The instructor's reference signs tokens with HS256 and gives the same
`JWT_SECRET_KEY` to every service. That is the simplest scheme, and it has one
serious flaw: every service that can *verify* a token can also *mint* one. Any
service bug that leaks configuration lets an attacker forge an administrator's
token. With eight services written in two languages by several people, that
risk is real.

A signed token on its own cannot be withdrawn before it expires. A revocation
check against Redis on every request is what makes "sign out" and "deactivate
user" take effect at once. That check is also a new failure mode in every
service.

## Decision

**auth-service is the only issuer.**
- It signs **RS256** access tokens with a private key that only it reads, from
  a file path in its configuration, never committed.
- It publishes the public key at `GET /v1/jwks.json`. Every other service
  verifies tokens with that public key and accepts **only** `RS256`.
- Access tokens live 15 minutes and carry `sub`, `sid`, `jti`, `iat`, `exp`,
  `role` and `permissions`. The permission names come from
  `web/middleware/authz.py`.

**Refresh tokens rotate.**
- They live 7 days and rotate on every use.
- Presenting an already-used refresh token is treated as theft: the whole
  session is deleted.

**A session is live only while `auth:session:{sid}` exists in Redis.** Every
protected request:
1. verifies the signature and `exp` locally;
2. checks that the session key exists in Redis (no PostgreSQL query);
3. enforces permissions from the claim.

Sign-out deletes the session key. Deactivating a user deletes all their
sessions through the `users.deactivated` event. If Redis is unreachable, a
protected request gets **503** (fail closed).

**Transport.** Tokens travel only as `Authorization: Bearer`. The web system
neither issues nor accepts them.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| HS256 with one shared secret, as in the reference | Every verifier can forge tokens, so a leak from any of eight services compromises all of them. RS256 costs one key pair and a JWKS endpoint, and PyJWT and the Rust `jsonwebtoken` crate support it equally |
| EdDSA (Ed25519) | Equally sound and with smaller keys. RS256 was chosen only for its wider support in tutorials and tooling the team will consult. Either is acceptable to a reviewer |
| A denylist of revoked `jti`s instead of a session allowlist | A denylist cannot express "this session is live", which is what the course demonstration checks. Revoking every token of a user would need every outstanding `jti`. Deleting one `sid` key revokes them all |
| Fail open when Redis is down (accept any validly signed token) | Revoked and deactivated users regain access during exactly the outage an attacker might cause. A 503 is the honest answer |
| The web system also switches to JWT and Redis | Contradicts the boundary in #398. ADR-0022 and ADR-0032 already give the web revocable sessions, so there is nothing to gain |
| Long-lived access tokens without refresh | Every stolen token would be valid for its whole lifetime unless Redis is consulted, and the refresh rotation is what limits a stolen refresh token |

## Consequences

**What this makes easy.**
- Revocation is immediate everywhere.
- Services never query PostgreSQL to authenticate.
- A compromised non-auth service cannot mint tokens.
- Both clients use one standard flow: Bearer, plus refresh on 401.

**What this makes hard.**
- **Redis is now on every request's critical path.** A Redis outage is a
  service outage for authenticated routes, and health checks must show it.
- **Key rotation needs a procedure.** The JWKS publishes the old and new keys
  during an overlap, with `kid` in the header. Without it, rotating signs every
  client out.
- **Clients need single-flight refresh,** so that concurrent 401s do not race
  and trip the reuse detection.
- **Permissions in the token are a snapshot.** A role change takes effect at
  the next refresh, at most 15 minutes later, unless the role change also
  revokes the user's sessions. users-service does this.

**What must now be true elsewhere.**
- ADR-0037 reserves the `auth:` prefix, writable by auth-service only and
  readable by all.
- F13-03 and F13-04 implement the same verification in both languages.
- `.env.example` documents the key-path variables.
- F18-04 proves rejection of `alg: none`, HS256, an expired token, a tampered
  token and a revoked token.

## Compliance

```bash
# Only auth-service touches a private key or signs; every verifier pins RS256.
grep -rnE 'jwt\.encode|encode\(&Header' services/ | grep -v '^services/auth-service/' \
  && echo "a service other than auth signs tokens" || true
grep -rnE 'algorithms=\[' services/ | grep -v '"RS256"' && echo "verifier not pinned to RS256" || true
grep -rnE 'HS256|JWT_SECRET' services/ contracts/ .env.example && echo "shared-secret JWT present" || true
```

F18-04 records the negative tests: a revoked session is rejected on the next
request with no PostgreSQL query.

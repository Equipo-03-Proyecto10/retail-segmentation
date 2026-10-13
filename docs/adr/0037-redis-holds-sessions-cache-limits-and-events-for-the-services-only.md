# ADR-0037 — Redis holds sessions, cache, limits and events, and only the services use it

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The second delivery requires Redis "in real operation" for "sessions, revoked
tokens, cache, counters and rate limits", consulted during authentication or
authorization. The services also need a way to tell one another that something
changed, without calling each other over HTTP
([ADR-0034](0034-eight-services-share-one-postgresql-each-the-sole-service-writer-of-its-tables.md)):

- a sale must invalidate the cached stock;
- a deactivation must end the user's sessions;
- a purchase must become an activity.

The web system stays out (#398). The instructor confirmed on 2026-10-13 that the requirement applies to the services, not to the web system (Q-12 in [`../scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md)). It has its own PostgreSQL sessions and gains
no dependency on Redis.

The design in [`../datastores/redis-design.md`](../datastores/redis-design.md)
was written for this delivery before its decisions existed. Its key names
predate the service boundaries. The decision here is which of its uses are
kept, under what namespace, and with what failure behaviour.

Two operational facts shape the setup:

- **CentOS Stream 10 ships Valkey, not Redis.** A Redis-compatible fork is not
  "Redis" for a course that names Redis.
- **One shared Redis is a shared blast radius.** Without ACLs, any service can
  delete another's sessions.

## Decision

**Deployment.**
- One Redis 7 instance, in a container pinned to an exact version
  ([ADR-0040](0040-services-redis-and-mongodb-run-as-containers-on-the-instance.md)).
- It is reachable only on the internal container network and never published.
- It is configured with `maxmemory 256mb` and `maxmemory-policy volatile-lru`,
  and AOF persistence `everysec`.

**Access.** The `default` user is disabled. Each service has one ACL user,
whose rights are limited to its prefixes:

| Prefix | Type | TTL | Writer | Readers |
|---|---|---|---|---|
| `auth:session:{sid}` | HASH (`sub`, `role`, `device`, `created_at`, `refresh_jti`) | refresh lifetime (7 d) | auth | all services |
| `auth:refresh_used:{jti}` | STRING | remaining refresh lifetime | auth | auth |
| `auth:user_sessions:{user_id}` | SET of `sid` | refresh lifetime | auth | auth |
| `auth:ratelimit:login:{email\|ip}` | STRING `INCR` | 15 min | auth | auth |
| `ratelimit:{service}:{sub}:{window}` | STRING `INCR` | 1 min | each service its own | same |
| `cache:catalog:*` | STRING | 15 min, deleted on write | catalog | catalog, catalog-rs |
| `cache:customers:*` | STRING | 10 min, deleted on write | customers | customers |
| `cache:inventory:{store}:{product}` | STRING | 2 min, deleted on `sales.recorded` | inventory | inventory |
| `cache:segments:customer:{id}` | STRING | 10 min (expiry only; the web emits no events) | segments | segments |
| `counter:reco_click:{label}:{yyyymmdd}` | STRING `INCR` | 90 d | segments | segments |
| `sales:idem:{key}` | STRING (response hash and id) | 24 h | sales | sales |
| `lock:{service}:{name}` | STRING `SET NX PX` | ≤ 30 s | each service its own | same |
| `stream:users.deactivated` | STREAM, `MAXLEN ~ 10000` | — | users | auth (group `auth`) |
| `stream:catalog.changed` | STREAM, `MAXLEN ~ 10000` | — | catalog | catalog-rs (group `catalog-rs`) |
| `stream:sales.recorded` | STREAM, `MAXLEN ~ 10000` | — | sales | inventory, activity (one group each) |

**Rules that hold across every key.**
- **Redis is never a source of truth.** Everything in it can be rebuilt, or is
  safe to lose: sessions force a sign-in, caches refill, counters restart.
- **Transactions are never cached.**
- **Failure behaviour.** An authentication check fails closed (503). A cache
  read fails open, falling through to PostgreSQL.
- **Event timing.** A stream event is appended only after the PostgreSQL
  commit it announces.
- **Consumer groups** acknowledge only after the event is handled, so that a
  restart re-delivers pending events.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Redis for inter-service messaging only | It leaves the course's session, revocation, cache, counter and rate-limit row unmet, and token revocation would need another store |
| Redis Pub/Sub for events | Fire-and-forget: a consumer that is restarting misses the event for good, and a stock cache would then stay wrong until its TTL. Streams with consumer groups re-deliver |
| Valkey from the CentOS repositories | Protocol-compatible, but the course names Redis, and using the container image keeps development and the instance identical |
| No ACLs, one password for all | Any service could delete another's sessions or poison its cache. ACL users cost one line per service |
| `allkeys-lru` eviction | It could evict a live session under memory pressure, signing users out at random. `volatile-lru` evicts only keys with a TTL, and every key here has one, except streams, which are capped by `MAXLEN` |
| The web system also uses Redis (sessions, login throttle) | Contradicts #398. The web's throttle and sessions already work per ADR-0032 and the existing login throttle |

## Consequences

**What this makes easy.**
- One place answers "is this session live", "how many attempts" and "what
  changed".
- ACLs make a misbehaving service fail with `NOPERM` instead of corrupting
  another service's state.
- Streams give at-least-once delivery without a broker.

**What this makes hard.**
- **Redis availability is now authentication availability**
  ([ADR-0036](0036-auth-service-issues-rs256-tokens-that-every-service-verifies-and-redis-can-revoke.md)).
- **Consumers must be idempotent,** because delivery is at least once.
  activity-service guarantees this with a unique index on the transaction id.
- **The segment cache can be stale for up to 10 minutes** after a run
  completes in the web. The segments contract states this.
- **ACL passwords are eight more secrets** to provision on the instance.

**What must now be true elsewhere.**
- [`../datastores/redis-design.md`](../datastores/redis-design.md) is rewritten
  as this specification (F13-05), and its "design only" banner goes.
- `.env.example` documents each ACL user's password variable with a local
  default.
- `docs/datastores/README.md` stops calling both stores unimplemented once
  F13-05 and F13-06 land.

## Compliance

```bash
# Inside the Redis container: the default user is off and each ACL is prefix-bound.
redis-cli --user svc_users --pass "$REDIS_USERS_PASSWORD" SET auth:session:x 1   # expect NOPERM
redis-cli --user svc_users --pass "$REDIS_USERS_PASSWORD" EXISTS auth:session:x  # expect 0, not NOPERM
redis-cli --user default PING                                                     # expect auth error
redis-cli --user admin --pass "$REDIS_ADMIN_PASSWORD" CONFIG GET maxmemory-policy # volatile-lru

# The web system never connects to Redis.
grep -rniE 'redis' web/ web/requirements.txt && echo "web depends on Redis" || true
```

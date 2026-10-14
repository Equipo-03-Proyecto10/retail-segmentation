# ADR-0037 — Two Redis instances carry sessions, caches, limits and the inter-service streams, for the services only

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The second delivery requires Redis "in real operation" for sessions, revoked tokens, cache, counters and rate limits, consulted during authentication. This set
adds a liveness check on every request ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)) and Redis Streams relayed
from each owner's outbox ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)).

These uses need opposite things under memory pressure. A lost session or liveness key signs a user out; a lost stream entry is a revocation
never applied or a cache nobody clears; a lost cache entry costs one PostgreSQL read. An instance has one `maxmemory-policy`, and
`volatile-lru` would evict sessions, liveness keys and rate limits too, since they all carry a TTL. The web stays out: it is frozen, keeps
its PostgreSQL sessions, and only the services consult Redis (Q-12 in
[`../scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md)). CentOS Stream 10 ships Valkey ([`../infra.md`](../infra.md)),
but the course names Redis. A shared Redis is a shared blast radius: without ACLs, any of fifteen services could delete another's sessions
or write into another's stream. The sizing, 64 MB and 128 MB, is estimated; F18-01 records the real figures.

## Decision

The services use **two Redis instances**, `redis-state` and `redis-cache`. Both run in containers from the official Redis image, on the 7.4
line, pinned to an exact patch tag and its digest in `deploy/infra/compose.yaml`. Neither publishes a port. Both are reachable only on the
`mosaiq` network ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)).
- **`redis-state`** never evicts and persists to AOF. It holds everything whose loss is a defect: sessions, liveness, rate limits and streams.
- **`redis-cache`** evicts any key under pressure and persists nothing. It holds only what can be rebuilt from PostgreSQL or MongoDB.

Every service connects as its own ACL user, `svc_<suffix>`, which reads and writes its own prefixes, runs `EXISTS` on the published liveness
family, `XADD` only to its own streams, and the consumer-group commands only on the streams it consumes. `redis-state` failing fails
authentication closed; `redis-cache` failing falls through to the database. The web never connects to either.

### Instances

| | `redis-state` | `redis-cache` |
|---|---|---|
| `maxmemory` | 64 MB | 128 MB |
| `maxmemory-policy` | `noeviction` | `allkeys-lru` |
| Persistence | `appendonly yes`, `appendfsync everysec`, `save ""` | `appendonly no`, `save ""` |
| Container `mem_limit` | 96 MB (headroom for the AOF rewrite fork) | 160 MB |
| When full | Writes fail with `OOM`. Reads, including every liveness `EXISTS`, still work | The least recently used key goes, whatever its prefix |
| Holds | `auth:*`, `pub:auth:v1:live:*`, `<service>:rl:*`, `stream:*` | `<service>:cache:*`, `engagement:live:*` |

Both instances run the same image: the 7.4 line, at an exact patch tag and digest chosen in F13-05. 7.4 has ACL selectors (7.0) and
`WAITAOF` (7.2), which the outbox relay and auth's revocations use. Changing the tag is a pull request of its own.

### Keys

`<service>` below is the boundary token, the suffix of the service's PostgreSQL and ACL role: `users`, `products`, `categories`, `stores`,
`stock`, `customers`, `preferences`, `sales`, `segments`, `recommendations`, `engagement`, `checkins`, `campaigns` and `audit_trail`.

| Key | Instance | Type | TTL | Writer | Readers |
|---|---|---|---|---|---|
| `auth:session:{sid}` | state | HASH (`user_id`, `role`, `expires_at`, password fingerprint) | remaining absolute session lifetime (≤ 12 h) | auth | auth |
| `auth:user_sids:{user_id}` | state | SET of `sid` | remaining absolute lifetime of the user's newest session (≤ 12 h) | auth | auth |
| `auth:refresh:{sha256}` | state | HASH (`sid`, `state`) | remaining absolute session lifetime (≤ 12 h) | auth | auth |
| `auth:revoked:jti:{jti}` | state | STRING | until the revoked token's `exp` (≤ 10 min) | auth | auth |
| `auth:rl:login:{ip}`, `auth:fail:{sha256(email)}:{ip}` | state | STRING `INCR` | the limit's window (15 min for failures) | auth | auth |
| `pub:auth:v1:live:{sid}` | state | STRING | remaining absolute session lifetime (≤ 12 h), set at sign-in, never extended | auth | every service, `EXISTS` only |
| `<service>:rl:*` (`users:rl:pwd:{user_id}` included) | state | STRING `INCR` | the limit's window | that service | that service |
| `stream:<producer>.v1.<event>` | state | STREAM, `MAXLEN ~ 10000` | none; capped by length | the producer's outbox relay | its consumer groups ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)) |
| `<service>:cache:*` | cache | STRING | per contract: products 10 min, categories and stores 15 min, recommendations 120 s; segments keys carry the `run_id` | that service | that service |
| `engagement:live:{store_id}:{yyyymmdd}:{type}` | cache | STRING `INCR` | 48 h | engagement | engagement |

Every key on `redis-state` except a stream carries a TTL, so `noeviction` does not fill with leaked keys. Rate limits sit on `redis-state`,
because a limit LRU can evict is a limit an attacker can reset. A cache is invalidated by deleting its key or replacing a prefix's random
generation token, so evicting the token only causes misses. No service runs `SCAN`, `KEYS` or `RANDOMKEY`. The liveness family is the only
key family published for others ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md),
`contracts/auth/redis-liveness.v1.md`).

### Streams

`stream:<producer>.v1.<event>` keys live on `redis-state`. [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) lists each stream, what is captured into it
and its consumer groups, and says how entries are produced and acknowledged. A consumer group is named after the consuming service.

### ACLs

The `default` user is `off` on both instances. An `ops` user, its password held outside the repository, runs `ACL LOAD`, `CONFIG GET` and
monitoring; no service uses it. Each service commits `services/<name>/redis/<name>.acl`, one `user` line. The deploy job renders the hashed
password into the line, assembles every installed service's line into each instance's `aclfile`, and runs `ACL LOAD` on both instances, with
no restart. `ACL LOAD` is all or nothing.

Every service user has these rules:
- **Own prefixes, read and write:** `-@all +@connection +@read +@write -@dangerous -scan -randomkey`, with
  `~<service>:rl:* ~<service>:cache:*`. auth's patterns are `~auth:* ~pub:auth:v1:live:*`; engagement adds `~engagement:live:*`.
- **Liveness, for every service except auth:** `(%R~pub:auth:v1:live:* +exists)`. It can test a session, never read `auth:*` or delete a liveness key.
- **Producer:** `(~stream:<own streams> +xadd)`, plus `+waitaof`.
- **Consumer:** `(~stream:<consumed streams> +xreadgroup +xack +xgroup|create +xpending +xclaim)`, with read-write key permission
  (`~`, not `%R~`), because XREADGROUP updates the pending entries list. A consumer never holds `+xadd` on a stream it does not own.
- **auth also gets `+waitaof`,** for its revocations ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).

### Failure behaviour

| Failure | Behaviour |
|---|---|
| `redis-state` unreachable, or the liveness check takes over 200 ms | Every authenticated request returns **503 `auth_unavailable`**. Authentication fails closed and never falls back to the signature alone. Sign-in also returns 503 |
| `redis-state` full | New sessions, rate-limit counters and `XADD` fail. Existing sessions keep working. Each outbox holds its unrelayed rows until there is room |
| `redis-state` restarted | Sessions and streams come back from the AOF, including consumer-group positions and pending entries. At most about one second of writes is lost. The relay's `WAITAOF` keeps an event from being marked relayed inside that second, and auth's `WAITAOF` after every revocation keeps a sign-out from being undone inside that second |
| `redis-cache` unreachable | Reads fall through to PostgreSQL, or MongoDB for `engagement:live:*`, after a short client timeout. The response is the same, only slower |
| `redis-cache` restarted | It comes back empty. Caches refill on demand, and live counters are rebuilt from `engagement_daily` ([ADR-0038](0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md)) |

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| One instance with `volatile-lru` | Sessions, liveness keys and rate limits all carry a TTL, so this policy evicts them too. Under cache pressure, users would be signed out at random, and a full cache could reset a login limit |
| One instance with `noeviction` | Sessions are safe, but a cache that outgrows its estimate stops sign-ins and event relay for every service. Caches would need hard sizing that nobody can measure yet |
| Redis Pub/Sub for events | Fire and forget. A consumer that is restarting misses the message for good, so a revocation could be lost. Streams with consumer groups keep pending entries and redeliver them |
| Valkey from the CentOS Stream 10 repositories | Protocol-compatible, but the course names Redis. A host package would also follow the distribution's version rather than the compose file, would differ from the development setup, and would have to listen on an address the containers can reach |
| A replica or Sentinel for `redis-state` | On one VM, a replica does not survive the failure that matters, which is losing the VM. It would cost memory the 3,000 MB budget does not have ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)) |
| No ACLs, one shared password | Any service could delete another's sessions, read refresh tokens, or forge an event on another boundary's stream. ACL users cost one line per service |
| The web system also uses Redis for its sessions and login throttle | Contradicts Q-12 and the frozen web. ADR-0022, ADR-0032 and `web/services/login_throttle.py` already cover the web |

## Consequences

**What this makes easy.**
- The cache can use all of its memory without any risk to a session; eviction and persistence are set per instance.
- Key-level isolation: a service that touches another service's keys gets `NOPERM`, not a silently corrupted session; a wrong producer cannot `XADD` to another boundary's stream.
- A service's ACL changes with that service's deploy, through `ACL LOAD`, with no Redis restart and no other service redeployed.
- Losing `redis-cache` costs latency, never correctness.

**What this makes hard.**
- **`redis-state` is a single point of failure for authentication** in all fifteen services: when it is down, the whole API answers 503.
- **`noeviction` needs watching.** A key without a TTL, or a group that never acknowledges, grows until writes fail (F18-01 checks).
- **`MAXLEN ~ 10000` can drop events a slow consumer has not read.** The backstops are the cache TTLs and auth's 60 s reconcile.
- **Consumer-group names are not enforced.** A consumer of a shared stream can read or acknowledge as another group. The kit takes the group name only from its own service's name, and cache TTLs bound the damage.
- **The ACL cannot keep a cache key off `redis-state`,** because one line is loaded on both instances; the TTL scan catches it after the fact.
- **`ACL LOAD` replaces every user at once.** The file is assembled from every installed service; one invalid line blocks every ACL change.
- **Two more containers and sixteen more secrets,** one password per service plus `ops`; 256 MB of the budget goes to Redis.
- **The image is pinned, so security patches are manual.** Somebody has to bump the tag.

**What must now be true elsewhere.**
- [ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md) uses only `auth:*` and `pub:auth:v1:live:*`, on `redis-state`.
- [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md) lists the streams, and these ACLs grant exactly its producers and consumers.
- [ADR-0038](0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md) keeps live counters under `engagement:live:*` on `redis-cache` and rebuilds them from MongoDB.
- [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md) runs both instances, counts their 96 MB and 160 MB, and its deploy job installs `redis/<name>.acl`.
- `.env.example` documents `REDIS_STATE_URL`, `REDIS_CACHE_URL`, one `REDIS_<SERVICE>_PASSWORD` per service and `REDIS_OPS_PASSWORD`.
- [`../datastores/redis-design.md`](../datastores/redis-design.md) is rewritten as this specification (F13-05).

## Compliance

```bash
rs() { docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec -T redis-state redis-cli --no-auth-warning --user ops --pass "$REDIS_OPS_PASSWORD" "$@"; }
rc() { docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec -T redis-cache redis-cli --no-auth-warning --user ops --pass "$REDIS_OPS_PASSWORD" "$@"; }
rs CONFIG GET maxmemory-policy; rs CONFIG GET appendonly; rs CONFIG GET appendfsync   # noeviction, yes, everysec
rc CONFIG GET maxmemory-policy; rc CONFIG GET appendonly                              # allkeys-lru, no
docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec -T redis-state redis-cli PING   # default user off: NOAUTH or WRONGPASS, never PONG
for i in redis-state redis-cache; do docker compose -p mosaiq-infra -f deploy/infra/compose.yaml port "$i" 6379 && echo "FAIL: $i is published"; done
grep -cE 'image: redis:7\.4\.[0-9]+(-[a-z]+)?@sha256:[0-9a-f]{64}' deploy/infra/compose.yaml   # 2: exact patch and digest
grep -lE 'image: *redis' services/*/compose.yaml && echo "FAIL: a service runs its own Redis"
grep -rniE '\bredis\b' web/ && echo "FAIL: the web system references Redis (Q-12)"
```

`deploy/infra/check-redis.sh` runs the per-user checks, and prints nothing when they hold:
- every non-auth user may only `EXISTS` the liveness family, and is denied `DEL` on it, `auth:*`, other services' prefixes, `SCAN`, `KEYS`, `RANDOMKEY` and `FLUSHALL`;
- only a stream's producer may `XADD` to it, as named by `contracts/streams/<producer>.<event>.v1.md`;
- every non-stream key on `redis-state` has a TTL, and no `*:cache:*` key lives there.

The shared conformance suite stops `redis-state` and expects 503 `auth_unavailable` from every service image, then stops `redis-cache` and expects a 200 from
every cached read.

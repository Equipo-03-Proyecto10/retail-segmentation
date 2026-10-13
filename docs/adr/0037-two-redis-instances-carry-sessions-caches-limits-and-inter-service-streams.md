# ADR-0037 — Two Redis instances carry sessions, caches, limits and the inter-service streams, for the services only

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The second delivery requires Redis "in real operation" for sessions, revoked
tokens, cache, counters and rate limits, consulted during authentication. Two
decisions in this set add to that:
- auth-service keeps every session in Redis, and every other service checks
  that the session is live on each request
  ([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).
- Every event between services travels on Redis Streams, relayed from the
  owner's outbox
  ([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)).

These uses need opposite things from memory pressure:
- A lost session signs a user out. A lost liveness key turns every request of
  that session into a 401.
- A lost stream entry is an event that never arrives: a revocation auth never
  applies, or a cache nobody clears.
- A lost cache entry is a miss, and costs one PostgreSQL read.

A Redis instance has exactly one `maxmemory-policy`. The earlier draft of this
record used one instance with `volatile-lru`, on the reasoning that only keys
with a TTL are evicted. That reasoning was wrong: a session, a liveness key and
a rate-limit counter all carry a TTL, so `volatile-lru` evicts them as readily
as a cached product.

Three further facts shape the setup:
- **The web system stays out.** It is frozen, keeps its PostgreSQL sessions
  ([ADR-0022](0022-sessions-are-resolved-server-side-on-every-request.md),
  [ADR-0032](0032-server-side-sessions-have-idle-and-absolute-lifetimes.md)),
  and the instructor confirmed on 2026-10-13 that only the services consult
  Redis (Q-12 in
  [`../scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md)).
  `web/` contains no Redis client today.
- **CentOS Stream 10 ships Valkey, not Redis**
  ([`../infra.md`](../infra.md) records the image). Valkey speaks the same
  protocol, but the course names Redis.
- **A shared Redis is a shared blast radius.** Fifteen services reach the same
  instance. Without ACLs, any of them can delete another's sessions, read
  auth's refresh tokens, or write into another boundary's stream.

What is uncertain is the sizing. 64 MB for sessions and streams, and 128 MB for
caches, are estimates for a course demonstration, not measurements. F18-01
records the real figures.

## Decision

The services use **two Redis instances**, `redis-state` and `redis-cache`.
Both run in containers from the official Redis image, on the 7.4 line, pinned
to an exact patch tag and its digest in `deploy/infra/compose.yaml`. Neither
publishes a port. Both are reachable only on the `mosaiq` network
([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)).
- **`redis-state`** never evicts and persists to AOF. It holds everything whose
  loss is a defect: sessions, liveness, rate limits and streams.
- **`redis-cache`** evicts any key under pressure and persists nothing. It
  holds only what can be rebuilt from PostgreSQL or MongoDB.

Every service connects as its own ACL user, `svc_<boundary>`, defined in its
own ACL file and loaded with `ACL LOAD`. That user can read and write its own
prefixes. It can run `EXISTS` on the published liveness family, `XADD` only to
the streams its boundary produces, and the consumer-group commands only on the
streams it consumes. `redis-state` failing fails authentication closed.
`redis-cache` failing falls through to the database. The web system never
connects to either.

### Instances

| | `redis-state` | `redis-cache` |
|---|---|---|
| `maxmemory` | 64 MB | 128 MB |
| `maxmemory-policy` | `noeviction` | `allkeys-lru` |
| Persistence | `appendonly yes`, `appendfsync everysec`, `save ""` | `appendonly no`, `save ""` |
| Container `mem_limit` | 96 MB (headroom for the AOF rewrite fork) | 160 MB |
| When full | Writes fail with `OOM`. Reads, including every liveness `EXISTS`, still work | The least recently used key goes, whatever its prefix |
| Holds | `auth:*`, `pub:auth:v1:live:*`, `<service>:rl:*`, `stream:*` | `<service>:cache:*`, `engagement:live:*` |

The 7.4 line is chosen because it has ACL selectors (7.0) and `WAITAOF` (7.2),
which the outbox relay uses
([ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)).
Both instances run the same image. Changing the tag is a pull request of its
own.

### Keys

`<service>` below is the boundary token, the suffix of the service's PostgreSQL
and ACL role: `users`, `products`, `categories`, `stores`, `stock`,
`customers`, `preferences`, `sales`, `segments`, `recommendations`,
`engagement`, `checkins`, `campaigns` and `audit_trail`.

| Key | Instance | Type | TTL | Writer | Readers |
|---|---|---|---|---|---|
| `auth:session:{sid}` | state | HASH | refresh lifetime (7 d) | auth | auth |
| `auth:user_sids:{user_id}` | state | SET of `sid` | refresh lifetime | auth | auth |
| `auth:refresh:{sha256}` | state | STRING | remaining refresh lifetime | auth | auth |
| `auth:revoked:jti:{jti}` | state | STRING | until the revoked token's `exp` (≤ 10 min) | auth | auth |
| `auth:rl:login:{ip}`, `auth:fail:{sha256(email)}` | state | STRING `INCR` | the limit's window (15 min for failures) | auth | auth |
| `pub:auth:v1:live:{sid}` | state | STRING | the session's newest refresh lifetime | auth | every service, `EXISTS` only |
| `<service>:rl:*` | state | STRING `INCR` | the limit's window | that service | that service |
| `stream:<producer>.v1.<event>` | state | STREAM, `MAXLEN ~ 10000` | none; capped by length | the producer's outbox relay | the consumer groups below |
| `<service>:cache:*` | cache | STRING | per contract: products 10 min, categories and stores 15 min, recommendations 120 s; segments keys carry the `run_id` | that service | that service |
| `engagement:live:{store_id}:{yyyymmdd}:{type}` | cache | STRING `INCR` | 48 h | engagement | engagement |

Every key on `redis-state` except a stream carries a TTL. That is what keeps
`noeviction` from filling up with leaked keys. Rate limits sit on
`redis-state`, not the cache, because a limit that LRU can evict is a limit an
attacker can reset by generating cache traffic. A cache is invalidated by
deleting its named key, or by replacing a prefix's generation token with a
fresh random value. A random token, not a counter, means that evicting the
token can only cause misses, never bring back an older generation. No service
runs `SCAN`, `KEYS` or `RANDOMKEY`, which would list other services' key
names. The liveness family is the only key family one service
publishes for others. Its contract is `contracts/auth/redis-liveness.v1.md`
([ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).

### Streams

| Stream | Producer (captured from) | Consumer groups |
|---|---|---|
| `stream:users.v1.access_changed` | users (`app_user` UPDATE OF `is_active`, `role_id`, and DELETE) | `auth`: deletes every live session key of that user. auth also reconciles live sessions against `api_users.credential_v1` every 60 s |
| `stream:products.v1.changed` | products (`product`) | `products` (its own cache), `recommendations` |
| `stream:categories.v1.changed` | categories (`category`) | `categories` (its own cache), `recommendations` |
| `stream:stores.v1.changed` | stores (`store`, `stores.store_location`) | `stores` (its own cache) |
| `stream:stock.v1.changed` | stock (`inventory`) | `recommendations` |
| `stream:preferences.v1.changed` | customer-preferences (`customer_preferred_channel`, `customer_interest_category`) | `recommendations` |
| `stream:sales.v1.recorded` | sales (`transaction` INSERT, from the CSV path and the service) | `recommendations` |
| `stream:segments.v1.run_recorded` | segments (`segmentation_run` INSERT) | `segments` (its own cache), `recommendations` |

A consumer group is named after the consuming service. Each stream's contract
is `contracts/streams/<producer>.<event>.v1.md`
([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)).
How entries are produced, consumed and acknowledged is
[ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md).

### ACLs

The `default` user is `off` on both instances. An `ops` user, with its password
held outside the repository, runs `ACL LOAD`, `CONFIG GET` and monitoring. No
service uses it.

Each service commits `services/<name>/redis/<name>.acl`: one `user` line with
its rules and a password placeholder. The same line is loaded on both
instances. The deploy job:
1. renders the hashed password (`#<sha256>`) from the service's secret into
   the line;
2. assembles every installed service's line into each instance's `aclfile`;
3. runs `ACL LOAD` on both instances, with no restart.

`ACL LOAD` is all or nothing. If any line is invalid, the instance keeps its
previous users.

Every service user has these rules:
- **Own prefixes, read and write:** `-@all +@connection +@read +@write
  -@dangerous -scan -randomkey`, with its own key patterns, `~<service>:rl:*
  ~<service>:cache:*`. auth's patterns are `~auth:* ~pub:auth:v1:live:*`, and
  engagement's add `~engagement:live:*`.
- **Liveness, for every service except auth:** the selector
  `(%R~pub:auth:v1:live:* +exists)`. It can test a session, never read
  `auth:*`, and never delete a liveness key.
- **Producer:** the selector `(~stream:<own streams> +xadd)`, and `+waitaof`.
  It also gets `+xtrim` only if it trims outside `XADD`; none does today.
- **Consumer:** the selector `(~stream:<consumed streams> +xreadgroup +xack
  +xgroup|create +xpending +xclaim)`. **A consumer never holds `+xadd` on a
  stream it does not own.**

For example, recommendations consumes six streams and produces none:

```text
user svc_recommendations on #<sha256> resetchannels -@all +@connection
  +@read +@write -@dangerous -scan -randomkey ~recommendations:rl:* ~recommendations:cache:*
  (%R~pub:auth:v1:live:* +exists)
  (~stream:products.v1.changed ~stream:categories.v1.changed ~stream:stock.v1.changed
   ~stream:preferences.v1.changed ~stream:sales.v1.recorded ~stream:segments.v1.run_recorded
   +xreadgroup +xack +xgroup|create +xpending +xclaim)
```

The line is wrapped here for reading. In the file it is a single line.

### Failure behaviour

| Failure | Behaviour |
|---|---|
| `redis-state` unreachable, or the liveness check takes over 200 ms | Every authenticated request returns **503 `auth_unavailable`**. Authentication fails closed and never falls back to the signature alone. Sign-in also returns 503 |
| `redis-state` full | New sessions, rate-limit counters and `XADD` fail. Existing sessions keep working. Each outbox holds its unrelayed rows until there is room |
| `redis-state` restarted | Sessions and streams come back from the AOF, including consumer-group positions and pending entries. At most about one second of writes is lost. The relay's `WAITAOF` keeps an event from being marked relayed inside that second |
| `redis-cache` unreachable | Reads fall through to PostgreSQL, or MongoDB for `engagement:live:*`, after a short client timeout. The response is the same, only slower |
| `redis-cache` restarted | It comes back empty. Caches refill on demand, and live counters are rebuilt from `engagement_daily` ([ADR-0038](0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md)) |

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| One instance with `volatile-lru` (the earlier draft) | Sessions, liveness keys and rate limits all carry a TTL, so this policy evicts them too. Under cache pressure, users would be signed out at random, and a full cache could reset a login limit |
| One instance with `noeviction` | Sessions are safe, but a cache that outgrows its estimate stops sign-ins and event relay for every service. Caches would need hard sizing that nobody can measure yet |
| Redis Pub/Sub for events | Fire and forget. A consumer that is restarting misses the message for good, so a revocation could be lost. Streams with consumer groups keep pending entries and redeliver them |
| Valkey from the CentOS Stream 10 repositories | Protocol-compatible, but the course names Redis. A host package would also follow the distribution's version rather than the compose file, would differ from the development setup, and would have to listen on an address the containers can reach |
| A replica or Sentinel for `redis-state` | On one VM, a replica does not survive the failure that matters, which is losing the VM. It would cost memory the 3,000 MB budget does not have ([ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)) |
| No ACLs, one shared password | Any service could delete another's sessions, read refresh tokens, or forge an event on another boundary's stream. ACL users cost one line per service |
| The web system also uses Redis for its sessions and login throttle | Contradicts Q-12 and the frozen web. ADR-0022, ADR-0032 and `web/services/login_throttle.py` already cover the web |

## Consequences

**What this makes easy.**
- The cache can use all of its memory without any risk to a session.
  Eviction and persistence are set by instance, not argued key by key.
- A service that touches someone else's keys gets `NOPERM`, not a silently
  corrupted session or a forged event. A wrong producer cannot `XADD` to
  another boundary's stream.
- A service's ACL changes with that service's deploy, through `ACL LOAD`, with
  no Redis restart and no other service redeployed.
- Losing `redis-cache` costs latency, never correctness.

**What this makes hard.**
- **`redis-state` is a single point of failure for authentication** in all
  fifteen services. When it is down, the whole API answers 503. That is
  deliberate, but it is an outage.
- **`noeviction` needs watching.** A key written without a TTL, or a consumer
  group that never acknowledges, grows until writes fail. The Compliance scan
  catches the first. `used_memory` and `XPENDING` need a check in F18-01.
- **`MAXLEN ~ 10000` can drop events a slow consumer has not read.** A group
  that falls more than about ten thousand entries behind loses the oldest
  ones. The backstops are the cache TTLs and auth's 60 s reconcile, not
  Redis.
- **The ACL cannot keep a cache key off `redis-state`.** The same user line
  is loaded on both instances, so the instance a key lands on is decided by
  the client's connection. The Compliance scan checks it after the fact.
- **`ACL LOAD` replaces every user at once.** The deploy job must assemble the
  file from every installed service, never just the one being deployed. One
  invalid line blocks every ACL change until it is fixed.
- **Two more containers and sixteen more secrets:** one password per service,
  plus `ops`. 256 MB of the memory budget goes to Redis.
- **The image is pinned, so security patches are manual.** Somebody has to
  bump the tag.

**What must now be true elsewhere.**
- [ADR-0036](0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)
  uses only `auth:*` and `pub:auth:v1:live:*`, on `redis-state`.
- [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)
  relays to and consumes from exactly the streams listed here.
- [ADR-0038](0038-mongodb-holds-in-store-engagement-owned-by-engagement-service-alone.md)
  keeps live counters under `engagement:live:*` on `redis-cache` and rebuilds
  them from MongoDB.
- [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)
  runs both instances in `deploy/infra/compose.yaml` and counts their 96 MB and
  160 MB in the memory budget. Its deploy job installs `redis/<name>.acl`.
- `.env.example` documents `REDIS_STATE_URL`, `REDIS_CACHE_URL`, one
  `REDIS_<SERVICE>_PASSWORD` per service and `REDIS_OPS_PASSWORD`, each with a
  local default.
- [`../datastores/redis-design.md`](../datastores/redis-design.md) is rewritten
  as this specification, and loses its "design only" banner (F13-05).

## Compliance

```bash
rs() { docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec -T redis-state redis-cli --no-auth-warning --user ops --pass "$REDIS_OPS_PASSWORD" "$@"; }
rc() { docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec -T redis-cache redis-cli --no-auth-warning --user ops --pass "$REDIS_OPS_PASSWORD" "$@"; }

# Policy and persistence on both instances.
rs CONFIG GET maxmemory-policy   # noeviction
rs CONFIG GET appendonly         # yes
rs CONFIG GET appendfsync        # everysec
rc CONFIG GET maxmemory-policy   # allkeys-lru
rc CONFIG GET appendonly         # no

# The default user is off, and neither instance publishes a port.
docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec -T redis-state redis-cli PING   # NOAUTH or WRONGPASS, never PONG
docker compose -p mosaiq-infra -f deploy/infra/compose.yaml port redis-state 6379 && echo "FAIL: redis-state is published"
docker compose -p mosaiq-infra -f deploy/infra/compose.yaml port redis-cache 6379 && echo "FAIL: redis-cache is published"

# The image is pinned to an exact patch and digest, and only infra runs Redis.
grep -cE 'image: redis:7\.4\.[0-9]+(-[a-z]+)?@sha256:[0-9a-f]{64}' deploy/infra/compose.yaml   # 2
grep -lE 'image: *redis' services/*/compose.yaml && echo "FAIL: a service runs its own Redis"

# Every service user: liveness EXISTS only, nothing of auth's, nobody else's prefix, no SCAN.
set -f   # keep the shell from expanding the * in "KEYS *"
for u in $(rs ACL USERS | grep '^svc_' | grep -vx svc_auth); do
  [ "$(rs ACL DRYRUN "$u" EXISTS pub:auth:v1:live:x)" = OK ] || echo "FAIL: $u cannot check liveness"
  for cmd in "DEL pub:auth:v1:live:x" "HGETALL auth:session:x" "GET auth:refresh:x" \
             "SET other:rl:x 1" "SCAN 0" "KEYS *" "RANDOMKEY" "FLUSHALL"; do
    [ "$(rs ACL DRYRUN "$u" $cmd)" = OK ] && echo "FAIL: $u may run $cmd"
  done
done

# Only a stream's producer may XADD to it; contracts/streams/<producer>.<event>.v1.md names it.
for f in contracts/streams/*.v1.md; do
  b=$(basename "$f" .v1.md); producer=${b%%.*}; stream="stream:${producer}.v1.${b#*.}"
  for u in $(rs ACL USERS | grep '^svc_'); do
    r=$(rs ACL DRYRUN "$u" XADD "$stream" '*' event_id x)
    if [ "$u" = "svc_$producer" ]; then [ "$r" = OK ] || echo "FAIL: $u cannot XADD $stream"
    else [ "$r" = OK ] && echo "FAIL: $u may XADD $stream, which it does not own"; fi
  done
done

# On redis-state, every key except a stream has a TTL, and no cache key lives there.
rs --scan | grep -v '^stream:' | while read -r k; do
  [ "$(rs TTL "$k")" -ge 0 ] || echo "FAIL: no TTL on $k"
done
rs --scan --pattern '*:cache:*' | head -1   # empty

# The web system never connects to Redis (Q-12).
grep -rniE '\bredis\b' web/ && echo "FAIL: the web system references Redis"
```

The shared conformance suite also stops `redis-state` and expects 503
`auth_unavailable` from every service image, then stops `redis-cache` and
expects a 200 from every cached read.

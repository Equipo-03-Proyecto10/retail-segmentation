# Redis — specification

The specification of [ADR-0037](../adr/0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md),
which governs where this document and the record disagree. The liveness key
family is [ADR-0036](../adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)'s,
the streams are [ADR-0044](../adr/0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)'s,
and [ADR-0040](../adr/0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md) runs the instances.

**Status.** F13-05 (#424) builds both instances in
[`deploy/infra/`](../../deploy/infra/README.md): their configuration, the ACL
assembly and the checks. No service connects yet. Each service's ACL line and
its `REDIS_<SUFFIX>_PASSWORD` arrive with that service's implementation story.

Redis is for the services only. The web system keeps its PostgreSQL sessions
and login throttle, and never connects (Q-12 in
[`../scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md)):
`grep -rniE '\bredis\b' web/` prints nothing.

## Instances

Two instances, because the two kinds of data need opposite things under memory
pressure. A lost session signs a user out. A lost cache entry costs one read.

| | `redis-state` | `redis-cache` |
|---|---|---|
| `maxmemory` | 64 MB | 128 MB |
| `maxmemory-policy` | `noeviction` | `allkeys-lru` |
| Persistence | `appendonly yes`, `appendfsync everysec`, `save ""`; the AOF on a named volume | `appendonly no`, `save ""` |
| Container `mem_limit` | 96 MB (headroom for the AOF rewrite fork) | 160 MB |
| When full | Writes fail with `OOM`. Reads, including every liveness `EXISTS`, still work | The least recently used key goes, whatever its prefix |
| Holds | `auth:*`, `pub:auth:v1:live:*`, `<service>:rl:*`, `stream:*` | `<service>:cache:*`, `engagement:live:*` |

Both run `redis:7.4.11-alpine`, pinned by its multi-architecture digest in
`deploy/infra/compose.yaml`. 7.4 has ACL selectors (7.0) and `WAITAOF` (7.2).
Both containers:
- run as the image's own `redis` user, with no capabilities and a read-only
  root filesystem;
- publish no port;
- join only the external network `mosaiq`.

Changing the tag is a pull request of its own.

## Keys

`<service>` is the boundary token: the suffix of the service's PostgreSQL and
ACL role (`users`, `products`, `categories`, `stores`, `stock`, `customers`,
`preferences`, `sales`, `segments`, `recommendations`, `engagement`,
`checkins`, `campaigns`, `audit_trail`).

| Key | Instance | Type | TTL | Writer | Readers |
|---|---|---|---|---|---|
| `auth:session:{sid}` | state | HASH (`user_id`, `role`, `expires_at`, password fingerprint) | remaining absolute session lifetime (≤ 12 h) | auth | auth |
| `auth:user_sids:{user_id}` | state | SET of `sid` | remaining absolute lifetime of the user's newest session (≤ 12 h) | auth | auth |
| `auth:refresh:{sha256}` | state | HASH (`sid`, `state`) | remaining absolute session lifetime (≤ 12 h) | auth | auth |
| `auth:refresh_family:{sid}` | state | SET of refresh-token SHA-256 values | remaining absolute session lifetime (≤ 12 h) | auth | auth |
| `auth:rl:login:{ip}`, `auth:fail:{sha256(email)}:{ip}` | state | STRING `INCR` | the limit's window (15 min for failures) | auth | auth |
| `pub:auth:v1:live:{sid}` | state | STRING | remaining absolute session lifetime (≤ 12 h), set at sign-in, never extended | auth | every service, `EXISTS` only |
| `<service>:rl:*` (`users:rl:pwd:{user_id}` included) | state | STRING `INCR` | the limit's window | that service | that service |
| `stream:<producer>.v1.<event>` | state | STREAM, `MAXLEN ~ 10000` | none; capped by length | the producer's outbox relay | its consumer groups |
| `<service>:cache:*` | cache | STRING | per contract: products 10 min, categories and stores 15 min, recommendations 120 s; segments keys carry the `run_id` | that service | that service |
| `engagement:live:{store_id}:{yyyymmdd}:{type}` | cache | STRING `INCR` | 48 h | engagement | engagement |

Rules for keys:
- **Every key on `redis-state` except a stream carries a TTL**, so
  `noeviction` cannot fill with leaked keys.
- **Rate limits live on `redis-state`.** A limit that LRU can evict is a limit
  an attacker can reset.
- **A cache is invalidated by deleting its key, or by replacing its prefix's
  random generation token.** Evicting the token only causes misses.
- **No service runs `SCAN`, `KEYS` or `RANDOMKEY`.**
- **The liveness family is the only key family published for others.** Its
  contract is `contracts/auth/redis-liveness.v1.md`.

## Streams

`stream:<producer>.v1.<event>` keys live on `redis-state`, one per
`contracts/streams/<producer>.<event>.v1.md`. ADR-0044 says what each stream
captures, who consumes it, and how entries are produced and acknowledged. A
consumer group is named after the consuming service.

## ACLs

The `default` user is `off` on both instances, so a client without credentials
gets `NOAUTH`. It is written `user default off ~* &* +@all`: Redis replays the AOF
as `default`, and `EXEC` re-checks permissions, so a restricted `default` drops
auth's `MULTI`/`EXEC` blocks and signs everyone out on a restart (ADR-0065).

The `ops` user's password, `REDIS_OPS_PASSWORD`, is held outside the
repository. It runs `ACL LOAD`, `CONFIG GET`, the checks and monitoring, and no
service uses it. Its rules are `+@all -@dangerous` with back only `ACL`,
`CONFIG GET`, `INFO`, `CLIENT LIST`, `SLOWLOG`, `LATENCY` and `MONITOR`, so it
cannot `FLUSHALL`, `CONFIG SET`, `DEBUG` or `SHUTDOWN`.

Each service commits `services/<name>/redis/<name>.acl`, one line, built from
these rules:

```text
# Every service: its own prefixes, read and write.
user svc_<suffix> on {{password}} resetchannels -@all +@connection +@read +@write -@dangerous -scan -randomkey ~<suffix>:rl:* ~<suffix>:cache:*

# Every service except auth: test a session, never read auth:* or delete a liveness key.
(%R~pub:auth:v1:live:* +exists)

# A producer: XADD to its own streams only, and WAITAOF for its relay.
+waitaof (~stream:<own streams> +xadd)

# A consumer: the group commands on the streams it consumes. Read-write (~, not %R~),
# because XREADGROUP updates the pending entries list. Never +xadd.
(~stream:<consumed streams> +xreadgroup +xack +xgroup|create +xpending +xclaim)
```

Two services add to the base line:
- **engagement** adds `~engagement:live:*` to its own patterns.
- **auth**'s line is:

  ```text
  user svc_auth on {{password}} resetchannels -@all +@connection +@read +@write -@dangerous -scan -randomkey ~auth:* ~pub:auth:v1:live:* +waitaof +multi +exec +discard +watch +unwatch (~stream:users.v1.access_changed +xreadgroup +xack +xgroup|create +xpending +xclaim)
  ```

  It gets `WAITAOF` for its revocations, and the transaction commands for its
  WATCH/MULTI/EXEC refresh rotation.

No other service gets a transaction command, and no service gets `@scripting`.

`{{password}}` is never a password. `deploy/infra/redis/assemble-acl.sh`
replaces it with `#<sha256>` of `REDIS_<SUFFIX>_PASSWORD`, read from
`/etc/mosaiq/svc/<name>.env`, where `<SUFFIX>` is the role suffix
upper-cased. A committed line that carries a password, a hash or an all-access
rule of its own is refused.

The script assembles every installed service's line, the `ops` line and the
disabled `default` line into one aclfile, which both instances load with
`ACL LOAD`, with no restart. It works under a `flock`, because concurrent
service deploys share that file. `ACL LOAD` is all or nothing. If either
instance refuses the new file:
- the previous file is restored;
- `redis-state` is reloaded from it if it had already taken the new one;
- the script exits 1.

So both instances always hold the same users. The cost is that one invalid
line blocks every ACL change until it is fixed.

## Failure behaviour

| Failure | Behaviour |
|---|---|
| `redis-state` unreachable, or the liveness check takes over 200 ms | Every authenticated request returns **503 `auth_unavailable`**. Authentication fails closed and never falls back to the signature alone. Sign-in also returns 503 |
| `redis-state` full | New sessions, rate-limit counters and `XADD` fail. Existing sessions keep working. Each outbox holds its unrelayed rows until there is room |
| `redis-state` restarted | Sessions and streams come back from the AOF, including consumer-group positions and pending entries. At most about one second of writes is lost. `WAITAOF` keeps a relayed event, or one of auth's revocations, from being lost inside that second |
| `redis-cache` unreachable | Reads fall through to PostgreSQL, or MongoDB for `engagement:live:*`, after a short client timeout. The response is the same, only slower |
| `redis-cache` restarted | It comes back empty. Caches refill on demand, and live counters are rebuilt from `engagement_daily` (ADR-0038) |

## Checks

`deploy/infra/check-redis.sh` runs ADR-0037's checks as `ops` and prints
nothing when they hold.

Per user, by `ACL DRYRUN`, so nothing is written:
- every `svc_*` user but `svc_auth` may `EXISTS` a liveness key, and may not
  `DEL` it, read `auth:session:*` or `auth:refresh:*`, or run `MULTI`;
- every `svc_*` user is denied:
  - `SET other:rl:x`;
  - `SCAN`, `KEYS`, `RANDOMKEY`, `FLUSHALL`;
  - `EVAL`;
- `svc_auth` may `MULTI`;
- only a stream's producer may `XADD` to it.

Per instance:
- the settings above;
- the default user is off, holds no password and answers `NOAUTH`;
- both instances hold the same users;
- every non-stream key on `redis-state` has a TTL, and no `*:cache:*` key lives
  there.

In CI, the `Infrastructure` workflow runs on every change under
`deploy/infra/`. It starts both instances and loads the fixtures in
`deploy/infra/tests/acl/`, then runs the checks and three negative tests:
- an over-broad `~*` line must fail the checks;
- an invalid line must leave both instances' users untouched;
- a key confirmed with `WAITAOF 1 0 1000` must survive a restart.

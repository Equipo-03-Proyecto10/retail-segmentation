# ADR-0056 — The disabled `default` user may replay the append-only file

**Status:** Proposed
**Owner:** Max
**Issue:** #714
**Supersedes:** [ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md), in part (the `default` user's line only)
**Superseded by:** —

---

## Context

[ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md) says a restart of `redis-state` brings sessions back from the AOF, and sets the
`default` user to `off resetkeys resetchannels -@all`. The two do not hold together. Redis replays the AOF through an internal client that runs as `default`. auth writes a session
inside `MULTI`/`EXEC`, and on replay the `EXEC` is checked against `default`'s rules and refused:

```
-NOPERM ACLs rules changed between the moment the transaction was accumulated and the EXEC call. [...] after processing the command 'exec'
```

Reproduced on `redis:7.4.11-alpine` with the repository's `redis-state.conf`: after a restart `auth:session:*` and `auth:user_sids:*` were gone, and only `pub:auth:v1:live:*`,
written by a plain `SET`, survived. Every authenticated request then fails, and the F18-04 negative suite has to run its `redis-state` case last because of it.

What was tried, each against a fresh data directory:

| `default` rules | After the restart |
|---|---|
| `-@all` (ADR-0037) | `EXEC` refused, transaction lost |
| `-@all +exec` | `EXEC` refused: the queued commands are checked too |
| `-@all +exec +@write ~*` | the transaction is replayed |

`+multi` is not needed. The user stays `off`, so nothing can authenticate as it: anonymous `PING` and `SET` return `NOAUTH`, and `AUTH default <anything>` returns `WRONGPASS`.

## Decision

The `default` user stays `off` on both instances and gains the rules the AOF replay needs: `user default off resetchannels -@all +exec +@write ~*`. It can run only write commands
and `EXEC`, over every key, and no connection can use it. This replaces the `default` line in ADR-0037's ACLs section and nothing else in that record. The line is rendered by
`deploy/infra/redis/assemble-acl.sh`.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep ADR-0037's line and accept that a restart revokes every session | That is the defect. It also makes the "sessions come back from the AOF" row of ADR-0037 false, and a restart during the demonstration (#462) signs everyone out |
| Stop auth using `MULTI`/`EXEC` | Session, user index and refresh family would no longer be written atomically, and the rotation depends on `WATCH`. A larger change to the service's one rule than to one line of ACL |
| `appendonly no` with a periodic RDB snapshot | Loses up to the snapshot interval of sessions and stream entries, against about one second now, and the relay's and auth's `WAITAOF` guarantees ([ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md)) stop meaning anything |
| Narrow `default`'s keys to `~auth:* ~pub:auth:v1:live:*` | Replay also rebuilds rate limits and streams, which other services own, so the narrow line would silently drop them. Since `default` cannot connect, the keys add no protection |

## Consequences

**What this makes easy.** A restart of `redis-state` or of the VM keeps sessions, streams and consumer-group positions, as ADR-0037 already claimed. The demonstration script no
longer needs the working assumption that nobody restarts Redis.

**What this makes hard.** The `default` user is no longer `-@all`, and a reviewer who reads that line cold may take it for the hole it looks like. The protection is `off`, which
Redis enforces at authentication, and the compliance checks below. If a Redis upgrade ever let an `off` user authenticate, this line would be a real exposure.

**What must now be true elsewhere.** `assemble-acl.sh` writes this line. `deploy/infra/tests/acl/` and `.github/workflows/infra.yml` check that `default` still refuses every
connection and that a `MULTI`-written key survives a restart. `docs/datastores/redis-design.md` and `deploy/infra/README.md` state the new rules. When this record is accepted,
ADR-0037's status becomes `Partially superseded by ADR-0056 (the default user's line only)`. The next service deploy loads the line with `ACL LOAD`, with no restart.

## Compliance

```bash
rs() { docker compose -p mosaiq-infra -f deploy/infra/compose.yaml exec -T redis-state redis-cli --no-auth-warning "$@"; }
rs PING                                  # NOAUTH, never PONG
rs AUTH default anything                 # WRONGPASS
rs --user ops --pass "$REDIS_OPS_PASSWORD" ACL GETUSER default | grep -A1 '^flags'   # off
```

`deploy/infra/check-redis.sh` fails if `default` is not `off`, or if `ACL DRYRUN default` allows anything outside `EXEC` and `@write`. The infra workflow writes a session with
`MULTI`/`EXEC` as `svc_auth`, waits for `WAITAOF`, restarts `redis-state` and expects the keys back.

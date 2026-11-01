# ADR-0065 — The disabled default user keeps every permission, so the AOF replay can re-run transactions

**Status:** Proposed
**Owner:** Max
**Issue:** #714
**Supersedes:** [ADR-0037](0037-two-redis-instances-carry-sessions-caches-limits-and-inter-service-streams.md), in part (the `default` user's rules)
**Superseded by:** —

---

## Context

ADR-0037 gives `redis-state` an AOF so that sessions, streams and consumer-group
positions survive a restart, and turns the `default` user off. `assemble-acl.sh`
wrote it as `user default off resetkeys resetchannels -@all`.

Redis replays the AOF as the `default` user. A plain command replays whatever the
ACL says, but `EXEC` checks permissions again, so every logged `MULTI`/`EXEC` block
is refused with `NOPERM ACLs rules changed between the moment the transaction was
accumulated and the EXEC call`. auth is the only service allowed `MULTI`
(ADR-0036), and it writes its sessions and refresh state that way. A restart of
`redis-state` therefore signed every user out, on the local stack and on the
instance (1,031 refusals after one local restart, 36 in the instance's log).

Reproduced on the pinned image (`redis:7.4.11-alpine`), AOF on, `save ""`, one user
allowed `MULTI`, `EXEC`, `SET`, `EXPIRE` and `GET`, `docker restart` between the
write and the read:

| `default` line | key set in `MULTI`/`EXEC` after restart | key set plainly | unauthenticated `PING` |
|---|---|---|---|
| `user default off resetkeys resetchannels -@all` | **missing**, 3 refused `EXEC`s | kept | `NOAUTH` |
| `user default off ~* &* +@all` | **kept**, 0 refused | kept | `NOAUTH` |

What is uncertain: whether a later Redis version replays as a superuser and makes the
line unnecessary. The image is pinned to an exact patch and digest, so this is
re-checked when the tag changes, and the restart check below fails if it regresses.

## Decision

The `default` user on both instances is `user default off ~* &* +@all`. It stays
disabled, so no client can authenticate as it and an unauthenticated client still
gets `NOAUTH`; the permissions exist only so that the AOF replay can run again what
was allowed when it was written. Nothing else in ADR-0037's ACL rules changes.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Give `default` only `+multi +exec` and the commands auth uses in transactions | `EXEC` re-checks every queued command, so the list must follow every change to auth's transactions, and a missed command drops sessions silently again |
| Rewrite auth's session writes as plain commands or Lua | Changes the refresh rotation of ADR-0036 and auth's code to work around a replay rule, and a later transaction in any service reopens the problem |
| Accept the sign-out after a restart | Contradicts ADR-0037's restart behaviour, and every VM reboot or `mosaiq-infra` restart ends all sessions |
| Leave `default` on with `nopass` | Opens the instance to any client on the network; the `off` flag is the protection |

## Consequences

**What this makes easy.** A restart of `redis-state` keeps sessions, streams and
pending entries, as ADR-0037 promised. The negative suite no longer has to run the
stop case last.

**What this makes hard.** The `default` user now holds every permission behind a
single flag. Turning it on, or giving it a password, would open the instance fully,
so the checks pin the flag and the absence of a password.

**What must now be true elsewhere.** `assemble-acl.sh` writes the line,
`check-redis.sh` checks it, and the infra workflow restarts `redis-state` and expects
a transaction to survive. `docs/datastores/redis-design.md` states it. The live
instance gets the line when the deploy job runs `assemble-acl.sh` (`ACL LOAD`), and
the next restart of `redis-state` replays the existing `EXEC` records.

## Compliance

- `deploy/infra/check-redis.sh` fails unless `default` is `off`, has no password and
  an unauthenticated `PING` answers `NOAUTH`.
- The `redis` job in `.github/workflows/infra.yml` writes a key in `MULTI`/`EXEC`,
  restarts `redis-state` and fails if the key is gone or the log holds `NOPERM ACLs
  rules changed`.
- `tests/negative/test_infrastructure.py` expects the sessions of the run to answer
  again after `redis-state` is stopped and started.

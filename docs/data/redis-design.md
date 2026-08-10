# Redis key namespace design

**Status:** Stub — awaiting S0-04b
**Owner:** Estefanía
**Story:** S0-04b · **Issue:** #22 · **Due:** Wed 2026-08-12
**Milestone 1 deliverable:** 9

---

> **This is a stub.** It carries the structure, the key patterns already fixed
> elsewhere, and the acceptance criteria, so the author starts from a shape
> rather than a blank file. S0-04b is an owned, estimated story and the design
> content is written there, not here.
>
> Deliverable 9 named Marcelo until 2026-08-09; it now names Estefanía, because
> S0-04b covers the MongoDB and Redis designs as one story and splitting its two
> halves across two people was an error in that table (#31).

## Context

*To be written in S0-04b.*

## Constraints already fixed elsewhere

| Constraint | Source |
|---|---|
| Redis holds sessions, the revocation denylist, chart cache, rate limits and locks — **nothing that cannot be rebuilt** | `postgresql-model.md` §5.4 |
| `session:{user_id}:{jti}`, `denylist:{jti}` and `refresh:{jti}` are the authentication key patterns | S0-05 checklist, `../adr/0001-authentication-and-token-lifecycle.md` |
| Session records are keyed by `session:{user_id}:{jti}` | `../scrum/01-product-backlog-epics.md` E-01 |
| One writer per namespace, assigned in ADR-002 | `AGENTS.md`, `../adr/0002-data-ownership-map.md` |

**The dependency to watch.** The three authentication namespaces are also
specified by ADR-001, which is Marcelo's and in flight. If the two documents
disagree on a key pattern or a TTL, ADR-001 wins for the authentication
namespaces and this document records the reference rather than a second
definition. Two documents defining one denylist is how a project ends up with
two revocation paths that disagree — the exact failure R-06 describes.

## Namespaces

The five namespaces S0-04b names, plus the authentication ones inherited from
ADR-001. Every pattern needs a documented TTL **and** an eviction expectation;
the second is the one usually skipped, and it is what decides whether the key
may be evicted under memory pressure or must survive.

| Namespace | Key pattern | TTL | Eviction expectation | Writer |
|---|---|---|---|---|
| Sessions | `session:{user_id}:{jti}` | *TBD in S0-04b, aligned to ADR-001* | | |
| Revocation denylist | `denylist:{jti}` | | | |
| Refresh tokens | `refresh:{jti}` | | | |
| Query cache | *TBD* | | | |
| Rate-limit counters | *TBD* | | | |
| Distributed locks | *TBD* | | | |

*Table to be completed in S0-04b.*

## What happens when Redis is unavailable

*To be written in S0-04b, consistent with ADR-001.* S0-05 requires ADR-001 to
state a single decision — fail closed or fail open — with a justification rather
than a list of options. This document must not state a different one.

## Memory policy

*To be written in S0-04b.* Which `maxmemory-policy` the deployment assumes, and
which namespaces would break if a key were evicted early.

## Acceptance criteria

Carried from S0-04b in `../scrum/03-sprint-00-backlog.md`. The document is not
complete until both hold.

- [ ] Given the Redis design, when reviewed, then **every key pattern has a documented TTL and eviction expectation**
- [ ] Given the design, when compared against ADR-001, then the authentication namespaces reference it rather than redefining it

## References

- `../scrum/03-sprint-00-backlog.md` — S0-04b
- `../adr/0001-authentication-and-token-lifecycle.md` — owns the authentication namespaces
- `postgresql-model.md` §5.4 — the store boundary
- `../adr/0002-data-ownership-map.md` — assigns the writing component per namespace
- `mongodb-design.md` — the other half of S0-04b

# ADR-0016 — The second delivery reinstates the distributed architecture, and the monolith becomes one component of four

**Status:** Proposed
**Owner:** Marcelo
**Issue:** —
**Supersedes:** [ADR-0001](0001-flask-monolith-on-a-single-vm.md), [ADR-0005](0005-document-mongodb-and-redis-designs-without-implementing-them.md)
**Superseded by:** —

---

## Context

The second-partial scope arrived, and it is not an extension of the first. It
requires microservices in individual containers, an Android client consuming
JSON, a desktop client consuming XML, MongoDB and Redis both in real operation,
JWT authentication shared across components, OpenAPI documentation, and
integration contracts specified before the clients are built.

Every one of those was retired by [ADR-0001](0001-flask-monolith-on-a-single-vm.md),
which recorded them as "retired rather than deferred" and rejected the
alternative of keeping them "marked as deferred". Its consequences section went
further and said deferred work would re-enter "as modules of this monolith
rather than as services". [ADR-0005](0005-document-mongodb-and-redis-designs-without-implementing-them.md)
committed the MongoDB and Redis designs on the explicit condition that no engine
is installed, no connection code is written, and no configuration variable for
either exists.

Both records were right for the delivery they governed and are wrong for the
next one. Neither can be repaired by editing: they are Accepted, and the
constraints they encode — C-1, C-2, C-3 and C-7 in
[`scope.md`](../scope.md) — are the first delivery's boundary, which stays
exactly where it is.

What forces the decision now is sequencing rather than urgency. The scope names
contracts as a prerequisite — *"antes de desarrollar las aplicaciones cliente,
el equipo deberá definir los contratos de integración"* — and a team that starts
writing services before the retirement is formally lifted will be building
against a repository that still says, in its authoritative records, that none of
this exists.

What is genuinely uncertain is how much of the monolith moves. The scope
requires that the web system keep working independently *and* that it grow —
more profiles, reports, Highcharts, file upload, pagination, notifications. It
also requires microservices that own authentication, users, catalogs and the
main process, which is what the monolith already does. Whether a capability is
extracted, duplicated, or fronted is a per-capability decision this record does
not make.

## Decision

The second delivery reinstates the distributed architecture: the Flask
application stops being the system and becomes one of four components alongside
a set of containerised microservices, an Android client and a desktop client,
with MongoDB and Redis in real operation and JWT as the shared authentication
mechanism. The monolith is not replaced — the scope requires it to keep working
independently — and no service, contract, schema or datastore is built before
the record that specifies it exists.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Amend ADR-0001 and ADR-0005 in place, since the team wrote them | The repository's own rule is supersede, never edit, and the reason applies exactly here: ADR-0001 is the record explaining why the delivered system looks the way it does. Editing it would erase the reasoning behind a system that still exists and is still being graded |
| Write no record and start building, since the scope document is itself the authority | The scope document says what to build, not what the team decided about its own prior decisions. Without this record, ADR-0001 remains the repository's answer to "are there microservices", and it answers no. A contributor reading the ADRs would be correctly informed and completely wrong |
| One ADR per component — microservices, mobile, desktop, MongoDB, Redis | Five records that all say "the second delivery requires it", none of which can be accepted without the others. The reinstatement is a single decision; the boundaries, contracts and per-service choices are separate decisions and get their own records as they are made |
| Rebuild the web system as microservices and retire the monolith | Directly contradicts the scope, which requires the web system to keep working independently and to grow. It would also discard a working, deployed, graded system to satisfy a requirement that does not ask for it |

## Consequences

**What this makes easy.** The team can start on contracts and service boundaries
without the repository contradicting them. The MongoDB and Redis designs already
in [`../datastores/`](../datastores/) stop being hypothetical and become the
starting point they were written to be — ADR-0005's actual bet, which this record
collects rather than discards.

**What this makes hard.** Almost everything, and it is worth being plain about
it. The first delivery's simplicity was the point of ADR-0001: one deployable
unit, one database engine, one place a request is handled. That is now gone.
JSON and XML between components, prohibited until now by C-2, become required,
and XML brings an XSD to keep in step with it. Authentication stops being a
session cookie in one process and becomes a token every component must validate
and Redis must be able to revoke, which is a new failure mode in every one of
them. Six to ten services in individual containers is six to ten deployment
units on a project whose current deployment is one systemd unit that a person
restarts. The single-administrator rule, enforced today in the application and
in a partial unique index, will need an answer for a world where several
services can write users.

**What must now be true elsewhere.** The second delivery needs its own scope
document; [`scope.md`](../scope.md) is titled "Scope — First delivery" and stays
that way, constraints included.
[`roadmap.md`](../roadmap.md) records the schedule and must stop describing the
shape as unknown. [ADR-0006](0006-run-under-both-systemd-and-docker-compose.md)
and [ADR-0015](0015-containers-are-a-development-path-only.md) say containers
have no deployment role; per-service containers are a deployment role, so
ADR-0015 will need superseding in turn once the second delivery's deployment is
decided — not before, because it correctly describes the delivered system today.
[ADR-0004](0004-model-ahead-of-the-deferred-segmentation-modules.md) ships
`customer.current_segment_id` as a known wrong shape awaiting a segment-history
module; a services split is the moment that debt comes due.

**What this does not decide.** Which services exist and where their boundaries
fall. What the contracts contain. Which capabilities move out of the monolith
and which are fronted. How JWT is issued, signed and revoked. Which of MongoDB
and Redis holds what. How the components are deployed and on what. Each is a
decision, and each gets its own record before it is built.

## Compliance

Until the records named above exist, this one is checkable by absence — the
repository must not contain an implementation the reinstatement has not yet
specified:

```bash
# No service skeletons, no client applications, no second deployable unit
# until the record that specifies it is accepted.
git ls-files | grep -vE '^(web|sql|deploy|docs|tests)/' | grep -vE '^(\.|README|AGENTS|CLAUDE|CONTRIBUTING|compose|Dockerfile)'

# The first delivery's boundary is unchanged: one application, no JSON or XML
# exchanged between internal components.
grep -rn "jsonify\|application/xml" web/ || echo "no internal JSON/XML exchange"
```

Once the second delivery begins, this record is satisfied by each subsequent ADR
naming it as the decision it inherits.

# ADR-0005 — MongoDB and Redis are documented as designs and not implemented

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #56 (F2-03)
**Supersedes:** —
**Superseded by:** —

---

## Context

The first-partial delivery document, `PrimeraEntrega-MOSAIQ_IAC.pdf`, lists its
expected deliverables on its first page. Three of them are things this
repository has explicitly retired:

| Delivery document asks for | Repository state |
|---|---|
| Diseño de MongoDB | [`issue-history.md`](../issue-history.md) #22: "Retired. One database engine only" |
| Diseño de Redis | Same record |
| Contenedores locales | [`issue-history.md`](../issue-history.md) #20: "Retired. No containers — gunicorn under systemd on the instance" |

The document's own designs assume more than that: JWTs with a revocation list,
an API with rate limiting, and mobile and desktop clients — all prohibited by
[`scope.md`](../scope.md) C-1, C-2 and §6.

So there are two mutually inconsistent statements of what the delivery is, and
both are real: the repository's scope was agreed and recorded in
[ADR-0001](0001-flask-monolith-on-a-single-vm.md), and the delivery document is
what the team submits and is graded on. The genuinely uncertain part is which
one the Product Owner considers binding, and that is not resolvable from inside
the repository — it is recorded as Q-5 in [`scope.md`](../scope.md) §8.

What is *not* uncertain is that the design work exists, is written, and would be
lost if the repository simply ignored it.

## Decision

The MongoDB and Redis designs are committed to
[`docs/datastores/`](../datastores/) as design documents, each marked as not
implemented and each stating which deferred module it belongs to. No engine is
installed, no connection code is written, no dependency is added, and no
configuration variable for either appears in `.env.example`. The running system
stays one Flask application over one PostgreSQL database, as
[ADR-0001](0001-flask-monolith-on-a-single-vm.md) decided.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Leave both designs out of the repository | They are graded deliverables that already exist. Keeping the repository tidy at the cost of losing submitted work is the wrong trade, and the next person to design these collections would start from nothing |
| Implement MongoDB and Redis | A direct contradiction of ADR-0001, C-3 and C-7, requiring superseding records, a rewritten scope and two more engines on the instance — three days before the delivery, for modules whose features are all deferred |
| Commit the Node.js connection files that came with them | The application is Python (ADR-0001). `db.mongo.js`, `db.redis.js` and `db.postgres.js` cannot run in this project at all; committing them would leave three files that look like configuration and are not |

## Consequences

**What this makes easy.** The design work is preserved, reviewable and
referenced from the roadmap, and the delivery document's Mongo and Redis
sections have a home in the repository. The running stack is unchanged, so
nothing about the deployment, the CI pipeline or the Definition of Done moves.

**What this makes hard.** The repository now contains documents describing
components that do not exist, which is exactly the kind of thing a reader
mistakes for a component. Every one of them opens with a status line saying so,
and they are quarantined in one directory with its own warning, but the risk
does not go to zero. The delivery document also still asks for container
execution, and this record does not answer that — nothing here makes the
demonstration's "ejecución mediante contenedores" item possible.

**What must now be true elsewhere.** Q-5 in [`scope.md`](../scope.md) §8 stays
open until the Product Owner rules. If the ruling is that the delivery document
is binding, this record is superseded and ADR-0001 goes with it. No requirements
file, dependency or environment variable may reference MongoDB or Redis while
this record stands.

## Compliance

```sql
-- No dependency, configuration or code for either engine.
-- Run from the repository root; each must print nothing.
grep -rniE 'mongo|redis' web/ .env.example
grep -rniE 'mongo|redis' web/requirements.txt web/requirements-dev.txt
```

Documents under `docs/datastores/` are exempt: describing the designs is the
point of this record.

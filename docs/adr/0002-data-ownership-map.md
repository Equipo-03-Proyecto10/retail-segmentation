# ADR-0002 — Data ownership map

**Status:** Proposed
**Date:** —
**Owner:** Raquel *(reassigned 2026-08-10, confirmed 2026-08-11 — `../scrum/sprints/sprint-00.md` §7.3)*
**Reviewer:** Estefanía
**Story:** S0-06
**Supersedes:** —
**Superseded by:** —

---

> **This is a stub.** It carries the structure and the checklist so the author
> starts from a shape rather than a blank file. S0-06 is an owned, estimated
> story and the decision content is written there, not here.

## Context

*To be written in S0-06.*

## Decision

*To be written in S0-06.*

## Must be specified

The checklist below is carried from S0-06 in `../scrum/03-sprint-00-backlog.md`.
The ADR is not complete until every item is resolved in the text above.

- [ ] Table-by-table matrix: owning component (writer), permitted readers
- [ ] The same matrix for MongoDB collections
- [ ] The same matrix for Redis key namespaces
- [ ] The rule for cross-component writes: prohibited; the owner exposes an endpoint instead

## Alternatives considered

*To be written in S0-06.*

## Consequences

*To be written in S0-06.*

## Compliance

*To be written in S0-06.*

## References

- `../scrum/03-sprint-00-backlog.md` — S0-06
- `../data/postgresql-model.md` — §5.4 store boundary
- `../../AGENTS.md` — the one-writer-per-table hard rule

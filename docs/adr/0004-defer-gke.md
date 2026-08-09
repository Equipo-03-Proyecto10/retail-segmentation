# ADR-0004 — Defer GKE until an independent scaling need is demonstrated

**Status:** Proposed
**Date:** —
**Owner:** Max
**Story:** — *(no Sprint 0 story; raised by R-15)*
**Supersedes:** —
**Superseded by:** —

---

> **This is a stub.** It carries the structure and the checklist so the author
> starts from a shape rather than a blank file. The decision content is not
> written here.

> **Checklist provenance.** Unlike ADR-0001, ADR-0002 and ADR-0003, this record
> has no "Must be specified" list in `../scrum/03-sprint-00-backlog.md`, because
> no Sprint 0 story produces it — it is referenced by S0-03 ("Rejected for M1:
> … Kubernetes (see ADR-004)") and by R-15 in the risk register, but never
> scheduled. The checklist below is therefore derived from R-15's mitigation
> text. **Someone needs to decide who owns this record and in which sprint it is
> written**; it is currently assigned to Max by inference from R-15's ownership,
> not by a planning decision.

## Context

*To be written.*

## Decision

*To be written.*

## Must be specified

Derived from R-15 in `../scrum/04-risk-register.md`. The ADR is not complete
until every item is resolved in the text above.

- [ ] The reading of "online clustering" in the requirements: an algorithm (incremental K-means over a transaction stream), not a container orchestrator
- [ ] What M1 requires instead: local `docker-compose` and Compute Engine
- [ ] The cost of adopting GKE now, stated concretely: Ingress, Secrets, service accounts, manifests, network debugging
- [ ] The specific condition that would justify revisiting: a demonstrated need for the incremental segmentation and drift services to scale independently of the rest of the platform
- [ ] When the condition is evaluated: end of M2, not before
- [ ] Who evaluates it and what evidence they need to see

## Alternatives considered

*To be written.*

## Consequences

*To be written.*

## Compliance

*To be written.*

## References

- `../scrum/04-risk-register.md` — R-15
- `../scrum/03-sprint-00-backlog.md` — S0-03, rejected dependencies
- `../scrum/01-product-backlog-epics.md` — E-33

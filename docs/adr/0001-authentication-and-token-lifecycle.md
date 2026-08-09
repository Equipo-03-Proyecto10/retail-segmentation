# ADR-0001 — Authentication and token lifecycle

**Status:** Proposed
**Date:** —
**Owner:** Marcelo
**Story:** S0-05
**Supersedes:** —
**Superseded by:** —

---

> **This is a stub.** It carries the structure and the checklist so the author
> starts from a shape rather than a blank file. S0-05 is an owned, estimated
> story and the decision content is written there, not here.

## Context

*To be written in S0-05.*

## Decision

*To be written in S0-05.*

## Must be specified

The checklist below is carried from S0-05 in `../scrum/03-sprint-00-backlog.md`.
The ADR is not complete until every item is resolved in the text above.

- [ ] Claim structure: `sub`, `jti`, `iat`, `exp`, `roles`, `permissions` or a permission version reference, `token_type`
- [ ] Access token TTL (15 minutes proposed) and refresh token TTL (7 days proposed)
- [ ] Refresh token rotation, with reuse of a rotated token treated as compromise
- [ ] CSRF protection for the cookie transport, given that `SameSite=Lax` alone is insufficient for state-changing POSTs
- [ ] Redis key patterns: `session:{user_id}:{jti}`, `denylist:{jti}`, `refresh:{jti}`
- [ ] Revocation semantics: logout, forced logout, permission change
- [ ] Signing algorithm and key rotation policy
- [ ] What happens when Redis is unavailable — fail closed or fail open, and why

## Alternatives considered

*To be written in S0-05.*

## Consequences

*To be written in S0-05.*

## Compliance

*To be written in S0-05.*

## References

- `../scrum/03-sprint-00-backlog.md` — S0-05
- `../scrum/01-product-backlog-epics.md` — E-01
- `../scrum/04-risk-register.md` — R-06
- `../data/postgresql-model.md` — `user_account`, `role`, `user_role` (D-09, D-10)

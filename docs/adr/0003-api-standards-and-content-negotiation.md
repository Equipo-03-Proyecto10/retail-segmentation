# ADR-0003 — API standards and content negotiation

**Status:** Proposed
**Date:** —
**Owner:** Max
**Story:** S0-07
**Supersedes:** —
**Superseded by:** —

---

> **This is a stub.** It carries the structure and the checklist so the author
> starts from a shape rather than a blank file. S0-07 is an owned, estimated
> story and the decision content is written there, not here.

## Context

*To be written in S0-07.*

## Decision

*To be written in S0-07.*

## Must be specified

The checklist below is carried from S0-07 in `../scrum/03-sprint-00-backlog.md`.
The ADR is not complete until every item is resolved in the text above.

- [ ] URL versioning: `/api/v1/...`
- [ ] Content negotiation: `Accept: application/json` and `Accept: application/xml`, with a defined default and a defined 406 behaviour
- [ ] JSON-to-XML mapping conventions: root element, collection element naming, null representation, date format
- [ ] Error envelope, identical in structure across both formats: code, message, details, correlation id
- [ ] HTTP status usage: 400, 401, 403, 404, 409, 422, 429, 500, 503
- [ ] Pagination convention
- [ ] Correlation identifier propagation: `X-Correlation-ID`, inbound reuse, generated when absent
- [ ] Rate limit headers and the 429 response shape
- [ ] Health endpoint contract: `/health/live`, `/health/ready`, `/health/database`, `/health/redis`, `/health/mongodb`, `/health/storage`, with the response schema and the meaning of degraded versus down

## Alternatives considered

*To be written in S0-07.*

## Consequences

*To be written in S0-07.*

## Compliance

*To be written in S0-07.*

## References

- `../scrum/03-sprint-00-backlog.md` — S0-07
- `../scrum/01-product-backlog-epics.md` — E-17, E-31
- `../scrum/04-risk-register.md` — R-07

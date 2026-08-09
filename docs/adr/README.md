# Architecture Decision Records

Architectural decisions for the Dynamic Segmentation and Retail Personalization
Platform. One decision per file, numbered in the order the decision was opened.

Governed by §10 of `../scrum/00-scrum-framework-charter.md`.

---

## The rules

**Numbered.** `NNNN-kebab-case-title.md`, zero-padded to four digits, allocated
in the order records are created. A number is never reused, even if the record is
rejected.

**Immutable once Accepted.** An Accepted record is never edited to change its
decision, its consequences, or its rationale. If the decision changes, write a
new record that supersedes it and set the old record's status to
`Superseded by ADR-NNNN`. That status line is the only edit permitted to an
Accepted record.

The point of the rule is that the record explains why the system is the way it is
*at the moment somebody decided it*, including the constraints and the
alternatives that looked reasonable then. Editing an Accepted ADR to match what
the team later learned destroys exactly the information a reader needs.

Correcting a typo or a broken link in an Accepted record is fine. Changing what
it decided is not.

**Decisions are not made silently in code.** If a pull request establishes an
architectural constraint that other components must respect, it needs an ADR or
it needs to reference one. The repository is the authoritative record.

---

## Statuses

| Status | Meaning |
|---|---|
| `Proposed` | Written, under review, not yet binding. Code must not depend on it. |
| `Accepted` | Binding. The record is immutable from this point. |
| `Rejected` | Considered and declined. Kept, because the reasoning is worth as much as an acceptance. |
| `Superseded by ADR-NNNN` | Replaced. The successor states what changed and why. |
| `Deprecated` | No longer relevant because the thing it governed was removed. |

---

## Index

| ADR | Title | Status | Owner | Story |
|---|---|---|---|---|
| [0001](0001-authentication-and-token-lifecycle.md) | Authentication and token lifecycle | Proposed | Marcelo | S0-05 |
| [0002](0002-data-ownership-map.md) | Data ownership map | Proposed | Estefanía | S0-06 |
| [0003](0003-api-standards-and-content-negotiation.md) | API standards and content negotiation | Proposed | Max | S0-07 |
| [0004](0004-defer-gke.md) | Defer GKE until an independent scaling need is demonstrated | Proposed | Max | R-15 |

ADR-0003 is owned by Max rather than Marcelo following the Sprint 0 rebalancing
recorded in `../scrum/03-sprint-00-backlog.md`: he implements the rate limiting
and health endpoints the contract specifies, so he is the correct owner of the
contract.

---

## Writing one

Copy [`template.md`](template.md) to `NNNN-your-title.md` and fill it in. Keep it
short. An ADR that takes twenty minutes to read does not get read, and the
decision it records gets re-litigated in a pull request six weeks later.

State the alternatives you rejected and why. A record with no rejected
alternatives reads as though the decision was obvious, and if it had been obvious
it would not have needed a record.

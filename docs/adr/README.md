# Architecture Decision Records

One decision per file, numbered in the order the decision was opened:
`NNNN-kebab-case-title.md`. A number is never reused.

**Immutable once Accepted.** An accepted record is never edited to change what
it decided. If the decision changes, write a new record that supersedes it and
set the old one's status to `Superseded by ADR-NNNN`. That status line is the
only edit an accepted record ever receives; fixing a typo or a broken link is
fine, changing the decision is not.

The point of the rule is that the record explains why the system is the way it
is *at the moment somebody decided it*, including the constraints that applied
then. Editing it to match what the team learned later destroys exactly the
information a reader needs.

**Decisions are not made silently in code.** A pull request that establishes a
constraint other work must respect needs an ADR, or a reference to one.

## Statuses

| Status | Meaning |
|---|---|
| `Proposed` | Written, under review, not binding. Code must not depend on it |
| `Accepted` | Binding, and immutable from this point |
| `Rejected` | Considered and declined. Kept, because the reasoning is worth as much as an acceptance |
| `Superseded by ADR-NNNN` | Replaced. The successor states what changed |

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-flask-monolith-on-a-single-vm.md) | Flask monolith on a single Compute Engine instance | Accepted |

## Writing one

Copy [`template.md`](template.md), number it, open a pull request. Keep it
short — an ADR that takes twenty minutes to read does not get read, and the
decision gets re-litigated in a pull request weeks later.

State the alternatives you rejected and why. A record with no rejected
alternatives has recorded a preference, not a decision.

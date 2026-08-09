# ADR-NNNN — <Short decision title, stated as the decision, not the topic>

**Status:** Proposed
**Date:** YYYY-MM-DD
**Owner:** <name>
**Story:** <S0-NN or the issue number that produced this decision>
**Supersedes:** — *(or ADR-NNNN)*
**Superseded by:** — *(filled in only when this record is replaced)*

---

## Context

What forces this decision now. The constraint, the requirement, or the conflict
between two documents that made a decision unavoidable. Include what is
genuinely uncertain — an ADR that reads as though the answer was obvious is
suspicious, and the reader needs to know which parts were judgement calls.

State the deadline pressure if there was any. A decision made under time
pressure is still a valid decision; a decision that pretends it was unhurried is
misleading.

## Decision

The decision, in the present tense and the active voice: *"We use one JWT
scheme with two transports."* One paragraph. If it takes three, the record is
probably carrying more than one decision and should be split.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| <the option a reasonable person would have picked> | <the specific reason, not "it was worse"> |
| <another> | |

At least one row. A record with no rejected alternatives has not recorded a
decision, it has recorded a preference.

## Consequences

**What this makes easy.**

**What this makes hard.** Be honest here. Every decision has a cost, and the
cost is the part a future reader needs, because they will be paying it.

**What must now be true elsewhere.** Other components, other documents, other
stories that inherit this decision and would break if it changed.

## Compliance

How a reviewer checks that an implementation actually follows this record. A
test name, a constraint, a script, a query — something executable if possible.
"By code review" is an acceptable answer only when nothing better exists.

## References

- Related ADRs, by number
- `docs/` paths this record depends on or contradicts
- External specifications, with the section

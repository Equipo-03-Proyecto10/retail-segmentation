# Sprint 0 — Record

**Dates:** 2026-08-10 → 2026-08-14 (5 working days)
**Planning held:** 2026-08-10 · **Present:** *to be recorded at Planning*
**Capacity:** 19 SP · **Committed:** 22 SP · **Commitment ratio:** 116%

> **Pre-filled on the morning of 2026-08-10, hours before Planning.** Sections 1, 2 and 5 are
> transcribed from `../03-sprint-00-backlog.md` v1.2 and are not new decisions.
> Sections 3, 4 and 7 are filled in during the ceremony. Anything already
> settled is marked; anything awaiting the room says so.

---

## 1. Sprint Goal

> One command brings up the full local stack against a frozen database schema,
> and every architectural contract the other three components depend on is
> written down and agreed.

---

## 2. Committed set

| Story | Title | Issue | Owner | SP | Priority | Depends on |
|---|---|---|---|---|---|---|
| S0-01 | Repository, organization and delivery board | #19 | Marcelo | 2 | P0 | — (blocks all) |
| S0-02 | Local container stack | #20 | Max | 3 | P0 | S0-01 |
| S0-03 | Flask application skeleton | #21 | Raquel | 3 | P0 | S0-02 |
| S0-04a | Frozen data model, bi-temporal traceability | #1, #14 | Estefanía (Marcelo reviews) | 3 | P0 | — (blocks S0-06, S0-08, all Sprint 1) |
| S0-04b | MongoDB and Redis design | #22 | Estefanía | 2 | P0 | S0-04a |
| S0-05 | ADR-001 Authentication and token lifecycle | #23 | Marcelo | 2 | P0 | — |
| S0-06 | ADR-002 Data ownership map | #24 | Estefanía | 1 | P0 | S0-04a |
| S0-07 | ADR-003 API standards and content negotiation | #25 | Max | 2 | P0 | — |
| S0-08 | Seed dataset with realistic transaction history | #26 | Estefanía | 3 | P0 | S0-04a |
| S0-10 | Engineering standards and Definition of Done | #28 | Raquel | 1 | P0 | — |
| **Total** | | | | **22** | | |

S0-09 and S0-11 moved to Sprint 1 in backlog v1.2 and are not committed here.

### 2.1 Already complete when the sprint opens

Four of the twenty-two points were finished over the weekend of 8–9 August,
which the release plan calls optional preparation and not planned capacity. The
burndown therefore opens with 4 SP already burned. That is recorded here rather
than left to look like an anomaly on the chart.

| Story | SP | Evidence |
|---|---|---|
| S0-04a | 3 | 27 tables / 3 views / 75 indexes applied from empty; all 16 checks behaved as specified; DDL and migration proven equivalent by schema diff. Full output on #14. Now enforced in CI (#32) |
| S0-10 | 1 | DoR and DoD published in charter §5 and §6; PR template carries the DoD verbatim; `black --check .` and `ruff check .` pass |

**Remaining for the five days: 18 SP against a 19 SP capacity line.**

### 2.2 Per-person load

The charter requires this check at Planning: anyone above their individual
capacity triggers reassignment **before** commitment, not during the sprint
(R-03 trigger). Individual capacity is 4.8 SP at the bootstrap anchor.

| Person | Stories | SP committed | SP remaining | Capacity | Ratio on remaining |
|---|---|---|---|---|---|
| Marcelo | S0-01 (2), S0-05 (2) | 4 | 4 | 4.8 | 0.83 |
| Estefanía | S0-04a (3), S0-04b (2), S0-06 (1), S0-08 (3) | 9 | 6 | 4.8 | **1.25** |
| Raquel | S0-03 (3), S0-10 (1) | 4 | 3 | 4.8 | 0.62 |
| Max | S0-02 (3), S0-07 (2) | 5 | 5 | 4.8 | **1.04** |
| **Total** | | **22** | **18** | **19** | 0.95 |

**Anyone above 1.0?** Estefanía and Max, and the aggregate only closes because
S0-04a is already done. Two things make the real position worse than 1.25:

1. **The labelling rule is still unestimated.** D-04 requires the mapping from
   K-means cluster index to `segment_label.code` to be a deterministic rule over
   centroid position. It is an algorithm to design, implement and test, not a
   line of DDL, and until it exists the migration report reports label noise as
   behaviour change (R-17). It falls to Estefanía, on top of 6 SP. **This is the
   first thing Planning Poker should price** — section 3 below.
2. **Estefanía has not accepted the organization invitation.** See section 5.

Backlog v1.2's conclusion stands: *"The sprint does not close on these numbers,
and no arrangement of them makes it close."* The existing mitigation — S0-08 may
extend into Monday of Sprint 1 without blocking anyone, since nothing in Sprint 1
week 1 depends on seeded data — moves 3 SP out of the week and is the lever to
pull first if the sprint is behind on Wednesday.

---

## 3. Estimates set at this Planning

Planning Poker outcomes for anything entering the sprint unestimated. Nobody
estimates alone (charter §9), so these belong to the room and not to this file
before the meeting.

| Story or work item | Estimate | Note |
|---|---|---|
| Deterministic cluster-to-label mapping rule (D-04, R-17) | *TBD* | Carried into the sprint unestimated by backlog v1.2. Owner is Estefanía by default; reassignment is on the table given the load above |
| | | |

---

## 4. Not committed, and why

| Story | SP | Reason | Where it went |
|---|---|---|---|
| S0-09 Baseline architecture diagrams | 2 | Below the capacity line; depends on S0-04a and S0-05 | Sprint 1, issue #27 |
| S0-11 Requirements and analysis skeleton | 1 | Below the capacity line | Sprint 1, issue #29 |

---

## 5. Known risks and dependencies entering the sprint

| Risk or dependency | Reference | Owner | Trigger to watch |
|---|---|---|---|
| **Estefanía has not accepted the organization invitation** | #30 | Marcelo | Invitation sent 2026-08-09 14:16, still pending on the morning of Planning. She owns 6 of the 18 remaining points. **Decide the contingency at this Planning, not later in the week** — see §7.3 |
| Segment model regression after the freeze | R-01, exp. 15 | Estefanía | Now enforced by CI on every schema change (#32); a failing `Schema ERD` job is the trigger |
| Marcelo is the bottleneck | R-03, exp. 16 | Marcelo | Assigned points exceed individual capacity → reassign before committing. Already live: two of Estefanía's stories were completed by Marcelo before the sprint opened |
| Seed dataset unrealistic | R-04, exp. 15 | Estefanía | Fewer than 2,000 customers or fewer than 18 months after loading → raise immediately |
| Non-deterministic labelling | R-17, exp. 15 | Estefanía | The unestimated rule above. Unresolved by Friday → Sprint 2 blocker discovered in Sprint 0 |
| Estimation optimism | R-14, exp. 12 | Whole team | Commitment ratio is 116% before the labelling rule is priced |
| S0-02 blocks S0-03 blocks Sprint 1 | backlog §"Why this sprint is serialized" | Max, Raquel | S0-02 not healthy by Tuesday → S0-03 cannot start and Sprint 1 slips |

**Process debt carried in.** No pull request in this repository has been
reviewed. DoD items 1 and 2 have never been met, because until 2026-08-09 there
were two people in the organization. `develop` still requires zero approvals.
Raising it to one is now possible and is a Planning decision, not a unilateral
one — see section 7.

---

## 6. Assumptions taken in place of a Product Owner decision

Full register: `../assumption-register.md`. All eight are Open.

| ID | Question | Working assumption | Impact if wrong |
|---|---|---|---|
| A-06 | How many segment labels, and are they fixed or derived from `k`? | Six fixed labels, `k` 2–20, assigned by deterministic centroid rank | **Medium, and it has a deadline.** Blocks S0-08's injected ground truth, which must be expressed in labels rather than cluster indices |
| A-01 | Are loyalty enrollments in M1 scope? | No; form shown, persistence deferred to M2 | Low — 3 SP |
| A-02 | Seven differentiated screen sets, or is the matrix enough for four? | Matrix for seven, screens for three | Medium — 8–13 SP |
| A-03 | Single currency acceptable? | Yes, MXN | Low |
| A-04 | Must the desktop application write? | Read and export only in M3 | Medium |
| A-05 | Is a corrected run in scope? | Supported by the decision axis, no UI in M1 | Low |
| A-07 | Is customer-level PII required? | Pseudonymous, no real personal data | Low |
| A-08 | Retention for `audit_log` and snapshots? | Indefinite for M1 | Low |

A-06 is the one to resolve this week.

---

## 7. Decisions for the room

Not backlog items. Each needs an answer on Monday and none should be taken by
one person outside the ceremony.

1. **Price the labelling rule** (§3). Every other number in §2.2 is provisional until this has a value.
2. **Raise `develop` to one required approval?** Now viable — three members, two owners. Recommended for after Planning rather than during it, so today's setup work is not blocked mid-flight.
3. **Contingency for Estefanía's 6 points.** The invitation is still unaccepted as Planning opens, so this is a decision for today rather than a watch item. S0-06 is 1 SP and depends only on a frozen schema; S0-04b is 2 SP and blocks nothing inside the sprint. S0-08 is the one that must not be reassigned casually — it is 3 SP of the demonstration, and the person who owns the data model is the right person to build the data. Starting artifacts for all three exist (#38), so whoever picks one up is not starting from a blank file.
4. **Confirm the 22 SP commitment or descope to it.** The charter now records 116% honestly. Descoping is a legitimate answer; restating the number is not.

---

## 8. Sprint Goal outcome

*Filled in at Review on 2026-08-14, not at Planning.*

**Met / partially met / not met:** *…*

**If not fully met, the gap:** *…*

| Metric | Value |
|---|---|
| Committed SP | 22 |
| Completed SP | |
| Commitment reliability | |
| Stories spilled | |

Retrospective: `../retrospectives/sprint-00.md` — copy it from
`../templates/retrospective.md` at the start of the meeting. Sprint 0 uses
**Start / Stop / Continue** (charter §4.2).

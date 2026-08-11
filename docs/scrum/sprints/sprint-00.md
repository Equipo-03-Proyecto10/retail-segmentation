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

As planned, before the decisions in §7 were taken:

| Person | Stories | SP committed | SP remaining | Capacity | Ratio on remaining |
|---|---|---|---|---|---|
| Marcelo | S0-01 (2), S0-05 (2) | 4 | 4 | 4.8 | 0.83 |
| Estefanía | S0-04a (3), S0-04b (2), S0-06 (1), S0-08 (3) | 9 | 6 | 4.8 | **1.25** |
| Raquel | S0-03 (3), S0-10 (1) | 4 | 3 | 4.8 | 0.62 |
| Max | S0-02 (3), S0-07 (2) | 5 | 5 | 4.8 | **1.04** |
| **Total** | | **22** | **18** | **19** | 0.95 |

After §7.1 moved the labelling rule to Sprint 1 and §7.3 reassigned S0-06:

| Person | Stories remaining | SP remaining | Capacity | Ratio |
|---|---|---|---|---|
| Marcelo | S0-01 (2), S0-05 (2) | 4 | 4.8 | 0.83 |
| Estefanía | S0-04b (2), S0-08 (3) | 5 | 4.8 | **1.04** |
| Raquel | S0-03 (3), S0-06 (1) | 4 | 4.8 | 0.83 |
| Max | S0-02 (3), S0-07 (2) | 5 | 4.8 | **1.04** |
| **Total** | | **18** | **19** | 0.95 |

**Anyone above 1.0?** Estefanía and Max, both at 1.04 — one story-point of
overshoot each, which is inside the noise of a bootstrap anchor nobody has
calibrated yet. That is a different situation from the 1.25-and-rising the
sprint would have carried otherwise, and it is the position the sprint commits
at.

Two facts behind the change, both recorded in §7:

1. **The labelling rule left Sprint 0 rather than being priced into it.** It is
   pipeline implementation first needed in Sprint 2, and adding it here would
   have put 3 more points on the one person already over capacity. Issue #40,
   Sprint 1.
2. **S0-06 moved to Raquel**, not to Marcelo. Loading the declared single point
   of failure with a third story belonging to an absent teammate is R-03
   happening, not R-03 mitigated.

Backlog v1.2's conclusion — *"the sprint does not close on these numbers, and no
arrangement of them makes it close"* — was written against the 22-point set with
the labelling rule inside it. It closes now at 0.95 aggregate, because 4 points
were finished before the sprint opened and 3 moved to Sprint 1. **The commitment
was not reduced by restating it.** The existing S0-08 mitigation — it may extend
into Monday of Sprint 1 without blocking anyone — remains the first lever to pull
if the sprint is behind on Wednesday.

---

## 3. Estimates set at this Planning

Planning Poker outcomes for anything entering the sprint unestimated. Nobody
estimates alone (charter §9), so these belong to the room and not to this file
before the meeting.

| Story or work item | Estimate | Note |
|---|---|---|
| Deterministic cluster-to-label mapping rule (D-04, R-17) | **3 SP, proposed** | Issue #40. Not a team estimate — see the caveat below. Moved to Sprint 1 by §7.1 |

**The estimate is one voice, and the charter says that is not enough.** §9:
*"Nobody estimates alone, and nobody revises another person's estimate downward
without discussion."* R-14 — estimation optimism — is live at exposure 12. The
3 is published with its full reasoning on #40 specifically so it can be argued
with; confirm or replace it at Planning Poker rather than adopting it because it
is written down.

The reasoning in short: no schema change (`labelling_strategy` already exists),
no component crossing, a pure function whose real deliverable is its determinism
tests — the charter's definition of a 3. It becomes a **5** in one circumstance
only: if the `k ≠ 6` policy has to be designed inside the story. A-06 fixes six
labels and allows `k` from 2 to 20, and nothing yet says what happens at `k = 4`
or `k = 9`.

**Recommendation: fold the `k ≠ 6` rule into A-06's resolution.** A-06 already
has to be answered this week because it blocks the seed ground truth in #26.
Answering it once settles two stories and holds this one at 3.

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
| ~~Estefanía has not accepted the organization invitation~~ **Resolved 2026-08-10** | #30 closed | Marcelo | All four members are in, three as owners. #22, #26 and #40 assigned. The §7.3 contingency stood for part of one day; what survives it is the S0-06 ownership question, now open rather than decided |
| Segment model regression after the freeze | R-01, exp. 15 | Estefanía | Now enforced by CI on every schema change (#32); a failing `Schema ERD` job is the trigger |
| Marcelo is the bottleneck | R-03, exp. 16 | Marcelo | Assigned points exceed individual capacity → reassign before committing. Already live: two of Estefanía's stories were completed by Marcelo before the sprint opened |
| Seed dataset unrealistic | R-04, exp. 15 | Estefanía | Fewer than 2,000 customers or fewer than 18 months after loading → raise immediately |
| Non-deterministic labelling | R-17, exp. 15 | Estefanía | The unestimated rule above. Unresolved by Friday → Sprint 2 blocker discovered in Sprint 0 |
| Estimation optimism | R-14, exp. 12 | Whole team | Commitment ratio is 116% before the labelling rule is priced |
| S0-02 blocks S0-03 blocks Sprint 1 | backlog §"Why this sprint is serialized" | Max, Raquel | S0-02 not healthy by Tuesday → S0-03 cannot start and Sprint 1 slips |
| **S0-05 gates the Redis half of S0-04b** — edge missing from the plan until 2026-08-11 | R-06; `../../data/redis-design.md`, *"The dependency to watch"* | Marcelo | Authentication key patterns, TTLs and the Redis-unavailable decision not settled by **Wed 09:00** → S0-04b either slips or invents a second revocation path. The MongoDB half is not gated |

**Process debt carried in, and the gate that now stops it growing.** Eleven pull
requests have been merged in this repository and **not one has been reviewed**.
Definition of Done items 1 and 2 have never been met, because for most of that
history there were two people in the organization and one of them was doing the
work. `CONTRIBUTING.md` has promised "one approval minimum" since day one and
nothing enforced it: `develop` required **zero** approving reviews.

As of 2026-08-10 it requires **one**, with `enforce_admins` on so the rule binds
owners too (§7.2). The eleven merges already in history cannot be un-merged, and
they should be named at Friday's retrospective rather than quietly left behind —
they are the reason the team's first velocity figure rests on work no second
person checked.

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

Decisions 1 and 3 were taken on 2026-08-10 rather than left open, because the
sprint had already started and both were blocking assignment. Both are recorded
here and on their issues, and **both are reversible at Planning** — that is the
point of writing down the reasoning rather than only the outcome.

### 7.1 Labelling rule — priced at 3 SP and moved to Sprint 1 · taken

Issue **#40**, which this work had never had. Proposed at 3 SP with the full
argument on the issue; see §3 for why one person's estimate is not a team
estimate and what would make it a 5.

Moved to **Sprint 1**. Sprint 0's goal is the stack standing and the contracts
written; this is pipeline implementation first needed when the pipeline runs in
Sprint 2. Adding 3 points to a sprint at 116%, on the person already over
capacity, would be R-14 and R-03 in one move. Deferring costs nothing provided
the seed ground truth in #26 stays expressed in label codes rather than cluster
indices — a constraint already written into `../../data/seed-strategy.md`.

### 7.2 Raise `develop` to one required approval · taken 2026-08-10

Held back while the organization was short-handed, because with two members and
`enforce_admins` on it would have blocked the only person able to merge. That
constraint ended when the fourth member joined, and the setting was raised the
same day.

`develop` now requires **one approving review**, dismisses stale reviews on new
commits, requires conversation resolution, keeps linear history, and binds
administrators. This is what `CONTRIBUTING.md` has promised since day one and
what Definition of Done item 2 already required; the only change is that
something now checks it.

**Consequence to expect, and to accept.** Nobody merges their own work
unreviewed from here, including the Scrum Master. That is the point. If a pull
request sits unreviewed for more than 24 hours on a weekday it goes to the
Weekly Sync as a blocker (§9), rather than the rule being switched off.

### 7.3 Contingency for Estefanía's points · taken, then superseded the same day

**Estefanía joined the organization on 2026-08-10 and the condition this
contingency was written for no longer holds.** #22, #26 and #40 are assigned to
her. S0-04b's Wednesday fallback never triggers. What follows is kept as the
record of what was decided and why, because the sprint record is evidence of how
the team handled a blocker, not only of the state it ended in.

**One thing did not unwind: S0-06.** It is currently Raquel's and Raquel may
already have started. Returning it is not automatic and is not one person's call:

- *Leave it with Raquel* — reassigning a 1-point story twice in a day is churn for its own sake, and it is already in her name on the board.
- *Return it to Estefanía* — it is hers by the plan, and she owns the data model that ADR-002 maps.

Either way Marcelo reviews. The load table in §2.2 assumes Raquel keeps it, and
moving it back returns her to 0.62 and puts Estefanía at 1.25.

#### Resolved 2026-08-11 · S0-06 stays with Raquel, and Estefanía reviews it

Left open for a day and closed on day 2 rather than carried to Friday, because
S0-02 and S0-03 begin writing to stores this week against a hard rule in
`AGENTS.md` that points at this document.

**The contingency's reason expired; its effect is worth keeping.** The
invitation is no longer pending, so the premise of §7.3 is gone. What is not
gone is the arithmetic it produced: §2.2 closes at 0.95 aggregate with nobody
above 1.04. Moving S0-06 back puts Estefanía at **1.25** — the exact number this
contingency was written to prevent — and drops Raquel to 0.62. The reassignment
is worth keeping on the strength of the load table alone, independently of why
it was first made.

**Estefanía's two remaining stories are both on the critical path.** S0-04b is
due Wednesday and S0-08 is three points of the demonstration carrying the only
sanctioned spillover in the sprint. Adding a fourth story to that, on day 2 of
5, is R-03 and R-14 in one move.

**The one real argument for moving it back is answered by the review seat, not
the author seat.** ADR-002 maps a data model Estefanía designed, and that
context should not be absent from the document. Making her the **reviewer**
transfers the context without transferring the load — one point of author effort
stays with the person who has headroom, and the person who has the answers still
has to agree with the result before it merges.

**Estefanía replaces Marcelo as reviewer.** Marcelo is the declared single point
of failure (R-03, exposure 16) and has authored or reviewed every merged change
in this repository to date. §7.2 went live today; the first thing worth pointing
it at is a pull request that neither involves him nor waits on him.

| | Author | Reviewer | Load after |
|---|---|---|---|
| **S0-06 ADR-002** | Raquel | Estefanía | Raquel 0.83 · Estefanía 1.04 |

Recorded on #24. Reversible, like everything else in this section — if the room
disagrees at Wednesday's refinement, §2.2 needs its second column back.

The decision as originally taken:

| Story | SP | Decision |
|---|---|---|
| S0-06 ADR-002 | 1 | **Reassigned to Raquel, Marcelo reviews.** Depends only on a frozen schema, which is done and CI-enforced. `AGENTS.md` already enforces a hard rule pointing at this document, and S0-02 and S0-03 both start writing to stores this week, so a stub is actively in the way |
| S0-04b Mongo + Redis | 2 | **Held for Estefanía until Wednesday 09:00.** Blocks nothing in Sprint 0 — the reason it was split out. Half of it is coupled to ADR-001, so starting early risks a second definition of the revocation path (R-06). If still absent Wednesday: Redis half to Marcelo *if ADR-001 is complete*, MongoDB half to Max |
| S0-08 Seed dataset | 3 | **Stays hers.** 3 points of the demonstration, and its sanctioned mitigation — slipping to Monday of Sprint 1 — is a better lever than reassignment |

**Why Raquel took S0-06 and not Marcelo.** Marcelo is the declared single point
of failure (R-03, exposure 16) and already completed two of Estefanía's stories
before the sprint opened. A third would be the risk materialising, not the risk
managed. Raquel had the most headroom at 0.62, and her own S0-03 is dependency-
blocked behind S0-02 early in the week, so the slack is real rather than
notional. The move puts her at 0.83 and leaves Marcelo unchanged.

### 7.4 Confirm the 22 SP commitment or descope to it — for the room

The charter records 116% honestly. After §7.1 the sprint carries 18 remaining
points against 19 of capacity — 0.95 — without the commitment having been
restated downward. Descoping further is still a legitimate answer; restating the
number is not.

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

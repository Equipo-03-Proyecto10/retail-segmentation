# Sprint NN — Retrospective

**Date:** YYYY-MM-DD
**Facilitator:** <name>
**Present:** <names> · **Absent:** <names, or none>
**Format:** Start / Stop / Continue — *or* Mad / Sad / Glad

> Copy this file to `../retrospectives/sprint-NN.md` at the **start** of the
> meeting and fill it in during the 45 minutes, not afterwards. A retrospective
> written from memory on Sunday is a summary, not a retrospective.
>
> Format rotates per §4.2 of `../00-scrum-framework-charter.md`: Start / Stop /
> Continue for Sprints 0 and 2, Mad / Sad / Glad for Sprint 1. Delete the
> section you are not using.

---

## 1. Actions carried from the previous retrospective

Reviewed first, before any new discussion. An action that carries over twice is
either dropped or escalated as a risk in `../04-risk-register.md` — it is not
carried a third time.

| # | Action | Owner | Target sprint | Status | If not done, why |
|---|---|---|---|---|---|
| | | | | Done / Carried / Dropped / Escalated | |

*First retrospective of the project: write "None — first retrospective."*

---

## 2. Metrics

The numbers go in before the discussion, so the conversation argues with data
rather than with impressions. Definitions are §11 of the charter.

| Metric | Value | Notes |
|---|---|---|
| Committed SP | | From the sprint backlog at Planning, not as revised mid-sprint |
| Completed SP | | Only stories meeting every applicable Definition of Done item |
| Velocity | | = Completed SP |
| Commitment reliability | | = Completed ÷ Committed, as a percentage |
| Stories spilled | | Count |
| Defects found after acceptance | | Tests whether the Definition of Done is strong enough |
| Open blockers at sprint end | | |
| Mean blocker age | | Hours or days from raised to cleared |

**Velocity note.** The bootstrap anchor of 1 SP ≈ 2.5 hours is a planning device
and is discarded after Sprint 1 (§7.1). From Sprint 2 onward the next
commitment is set from measured velocity, not from the anchor.

---

## 3. Spillover

Spillover is expected and should be visible. §10.1 of the charter is explicit
that a board showing steady progress with recorded spillover is stronger
evidence than a suspiciously clean burndown. Record the cause honestly —
"underestimated" and "blocked on someone else" lead to different fixes.

| Story | SP | Owner | % complete | Cause | Carried to |
|---|---|---|---|---|---|
| | | | | | |

**Was the cause estimation, dependency, capacity, or scope discovered mid-sprint?**

*…*

---

## 4. Discussion — Start / Stop / Continue

*Used for Sprints 0 and 2. Everyone contributes to every column before anything
is discussed, so the first speaker does not set the frame.*

### Start

- *…*

### Stop

- *…*

### Continue

- *…*

---

## 4. Discussion — Mad / Sad / Glad

*Used for Sprint 1. Delete this section when using Start / Stop / Continue.*

### Mad

- *…*

### Sad

- *…*

### Glad

- *…*

---

## 5. Process observations that belong in the repository

Anything raised here that is a decision, a defect, or a scope question does not
stay in this document:

- An architectural decision → a numbered ADR in `../../adr/`
- A task, defect, or scope question → a GitHub issue
- A risk → `../04-risk-register.md`
- An unanswered Product Owner question → `../assumption-register.md`

| Observation | Where it was recorded | Reference |
|---|---|---|
| | | |

---

## 6. Committed improvement actions

**At most three.** Each has a named owner and a target sprint. Actions without
an owner are not actions.

| # | Action | Owner | Target sprint | How we will know it worked |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |

---

## 7. Sprint-level Definition of Done

§6.1 of the charter. Checked at the end of the retrospective, not assumed.

- [ ] Sprint Goal met, or the gap explicitly recorded in the Review notes
- [ ] `develop` merged to `main` and tagged `v0.<sprint>.0`
- [ ] All documentation deliverables assigned to the sprint are committed in `docs/`
- [ ] Retrospective actions recorded with owners

---

*Template: `docs/scrum/templates/retrospective.md`. Referenced by Appendix C of
the Scrum Framework Charter.*

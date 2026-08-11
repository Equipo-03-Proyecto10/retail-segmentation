# Assumption Register

**Project:** Dynamic Segmentation and Retail Personalization Platform
**Owner:** Raquel (Proxy Product Owner)
**Version:** 1.0
**Date:** 2026-08-08
**Review cadence:** Every Sprint Planning and every retrospective

The Product Owner is the course professor and is not available for planning,
refinement, or same-day scope answers (see `00-scrum-framework-charter.md` §2.2
and Risk Register R-02). Any question that would normally go to the Product
Owner is recorded here with the decision the team took anyway, the date, and the
cost of being wrong. **Work does not stop waiting for an answer.**

**The confirmation rule.** The Scrum Master sends the Product Owner one written
digest per week containing progress, open assumptions requiring confirmation,
and any scope decision that changes a milestone deliverable. **Silence from the
Product Owner for five working days is treated as confirmation of the recorded
assumption** (`00-scrum-framework-charter.md` §2.2).

An assumption stays `Open` until the Product Owner confirms it, contradicts it,
or the five-working-day rule elapses against a digest that named it. When it
resolves, record the date and the outcome in `Resolved` — do not delete the row.

---

## Register

| ID | Date | Question | Working assumption | Impact if wrong | Status | Resolved |
|----|------|----------|--------------------|-----------------|--------|----------|
| A-01 | 2026-08-10 | Are loyalty enrollments in M1 scope? | No. Public site shows enrollment form; persistence deferred to M2. | Low — 3 SP of rework | Open | — |
| A-02 | 2026-08-08 | Are seven differentiated screen sets required in M1, or is the permission matrix sufficient for four of them? | Matrix for seven, screens for three (R-05) | Medium — 8–13 SP | Open | — |
| A-03 | 2026-08-08 | Is single currency acceptable? | Yes, MXN; `currency_code` present but never varied | Low | Open | — |
| A-04 | 2026-08-08 | Must the desktop application write, or only read? | Read and export only in M3 | Medium — write paths need scoped authorization per D-10 | Open | — |
| A-05 | 2026-08-08 | Is a corrected segmentation run in scope, or is a bad run simply discarded? | Correction is supported by the decision axis but has no UI in M1 | Low — schema already permits it | Open | — |
| A-06 | 2026-08-08 | How many segment labels, and are they fixed by the business or derived from k? | Six fixed labels, `k` between 2 and 20, labels assigned by deterministic centroid rank. **The `k ≠ 6` half now has a worked policy — see the note below and #40** | Medium — changes `segment_label` seed data and the labelling rule | Open | — |
| A-07 | 2026-08-08 | Is customer-level PII required at all, or can the seed be fully pseudonymous? | Pseudonymous; no real personal data ever loaded (R-12) | Low | Open | — |
| A-08 | 2026-08-08 | Retention period for `audit_log` and RFM snapshots? | Indefinite for M1; policy documented in the privacy section | Low | Open | — |

---

## Notes

**A-01** is the worked example from Appendix D of the Scrum Framework Charter and
is carried here verbatim as the first real entry.

**A-02 through A-08** are the open assumptions recorded in §10 of
`docs/data/postgresql-model.md`. Their dates are the date that document recorded
them. None of them blocks the schema freeze; each is a question whose answer
changes seed data, scope, or a policy statement rather than the shape of a table.

**A-06 is the one with a deadline.** It needs an answer from Estefanía before
S0-08 generates the injected migration ground truth, because that ground truth
has to be expressed in segment labels rather than cluster indices (see R-04 and
D-04). A-06 resolving late does not block the freeze; it blocks validating
migration detection against known answers.

**A-06's `k ≠ 6` half, worked 2026-08-11 and tabled for Wednesday's
refinement.** `sprints/sprint-00.md` §3 recommended folding the `k ≠ 6` rule
into A-06's resolution so that one answer settles two stories. That is done and
the full argument is on **#40**; it stays `Open` here because confirming an
assumption is the Product Owner's call under the confirmation rule above, not
one team member's.

Most of it turned out to be discovery rather than decision — the frozen schema
had already answered it and nobody had read it back:

| Sub-question | Answer already in the frozen schema |
|---|---|
| May two clusters in one run share a label? | **No.** `ux_segment_run_label` is `UNIQUE (model_run_id, label_code) WHERE label_code IS NOT NULL` |
| May a cluster carry no label at all? | **Yes.** `segment.label_code` is nullable |
| Is `k` unbounded? | **No.** `ck_segmentation_model_run_k CHECK (k BETWEEN 2 AND 20)` |

What was genuinely undecided was only which labels are used below six, and
whether a production run may exceed six at all. The proposal: one integer
formula covering `k` = 2…6 that anchors `lost` and `champions` at the endpoints
and falls out as an exact bijection at `k` = 6, and `k` > 6 restricted to
`candidate` and `experiment` runs. The reason for that restriction is
`v_segment_migration`, which degrades asymmetrically against null labels — its
`WHERE from_label_code IS NOT NULL` drops customers moving *out* of an
unlabelled cluster from the report entirely, while customers moving *into* one
surface as `direction = 'unclassified'`. A production run at `k` = 9 would
undercount migrations in one direction and mislabel them in the other, silently.
That is R-17 arriving through a side door.

Enforcing the restriction in the database (`CHECK (purpose <> 'production' OR k
<= 6)`) is a schema change against a frozen schema, so it is a new Alembic
revision in Sprint 1 alongside #40 — not an edit, and not something to do this
week.

---

## Revision history

| Version | Date | Author | Change |
|---|---|---|---|
| 1.0 | 2026-08-08 | Raquel | Register created. A-01 from Appendix D of the charter; A-02 through A-08 from §10 of `docs/data/postgresql-model.md`. |

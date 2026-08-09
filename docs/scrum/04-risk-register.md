# Risk Register

**Project:** Dynamic Segmentation and Retail Personalization Platform
**Owner:** Marcelo (Scrum Master)
**Version:** 1.2
**Date:** 2026-08-09
**Review cadence:** Every retrospective

Scoring: Probability (1–5) × Impact (1–5) = Exposure. Exposure ≥ 12 requires an owned mitigation with a due date. Exposure ≥ 20 is escalated to the Product Owner.

---

## Exposure matrix

Probability down the side, impact across. Cells with several entries are where the project is actually exposed.

| P ↓ / I → | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| **5** | | | | | |
| **4** | | | R-02 R-05 R-11 R-14 | R-03 | |
| **3** | | R-13 | R-16 | R-06 R-07 R-09 R-18 | R-01 R-04 R-10 R-17 |
| **2** | | | | R-08 R-12 R-15 | |
| **1** | | | | | |

Bands: exposure ≥ 12 requires an owned mitigation with a due date, 8–11 is monitored at each retrospective, ≤ 6 is accepted.

Two clusters matter. The four risks at probability 4, impact 3 are all the same underlying problem — the team commits more than it can deliver, whether the cause is an absent Product Owner, seven specified profiles, competing coursework, or optimistic estimation. They are tracked separately because they need separate mitigations, but if commitment discipline holds, all four drop at once. Four risks at probability 3, impact 5 threaten the demonstration through unrealistic data, no rehearsal, non-deterministic labelling, or a schema regression that goes unnoticed.

R-01 sat above that cluster at exposure 20 for as long as its executable checks were monitored only by review. They now run in CI on every push and pull request that touches the schema, which is the automated control the register said was missing, so R-01 drops to probability 3 and joins the cluster rather than sitting above it. It is not lower than the others: the schema is correct today, and what remains is the risk of silently regressing it.

R-17 joins that cluster rather than sitting apart from it, and it belongs there for the same reason as the other two: it does not degrade the demonstration, it destroys it. A migration report that cannot distinguish customer behaviour from label reassignment is not a weaker version of the demonstration — it is the demonstration producing a confident number that means nothing.

## Active risks

| ID | Risk | P | I | Exp | Owner |
|---|---|---|---|---|---|
| R-01 | Bi-temporal segment model not frozen before Sprint 1 development starts | 3 | 5 | 15 | Estefanía |
| R-02 | Product Owner unavailable for scope decisions | 4 | 3 | 12 | Marcelo |
| R-03 | Marcelo holds three roles and becomes the bottleneck | 4 | 4 | 16 | Marcelo |
| R-04 | Seed dataset unrealistic; migrations not demonstrable | 3 | 5 | 15 | Estefanía |
| R-05 | Scope creep from seven specified profiles | 4 | 3 | 12 | Raquel |
| R-06 | Duplicated authentication: Flask sessions plus JWT | 3 | 4 | 12 | Marcelo |
| R-07 | XML content negotiation retrofitted after M2 | 3 | 4 | 12 | Max |
| R-08 | Board history fabricated near the deadline; process grade lost | 2 | 4 | 8 | Marcelo |
| R-09 | GCP billing, quota or permission problems surface in Sprint 2 | 3 | 4 | 12 | Max |
| R-10 | No demo rehearsal; demonstration fails live | 3 | 5 | 15 | Marcelo |
| R-11 | Team members' other courses consume the 15 h/week commitment | 4 | 3 | 12 | Whole team |
| R-12 | Personal data handling in a segmentation platform breaches privacy expectations | 2 | 4 | 8 | Estefanía |
| R-13 | Docker image bloat from scientific Python; slow build and deploy cycles | 3 | 2 | 6 | Max |
| R-14 | Estimation optimism; systematic over-commitment | 4 | 3 | 12 | Whole team |
| R-15 | Premature adoption of Kubernetes consumes a week with no milestone benefit | 2 | 4 | 8 | Max |
| R-16 | M2 and M3 deadlines unknown; sequencing assumptions may be wrong | 3 | 3 | 9 | Marcelo |
| R-17 | Non-deterministic segment labelling reports noise as migration | 3 | 5 | 15 | Estefanía |
| R-18 | Application connects as schema owner, making the append-only audit trail decorative | 3 | 4 | 12 | Max |

---

## Detail and mitigation

### R-01 — Segment model not frozen before development (Exposure 15)
Sprint 1 stories that read or write segment data cannot be estimated until the assignment model is decided, and code written against a mutable `customer.segment_id` must be rewritten once traceability is introduced.

**Mitigation.** S0-04a is timeboxed to two days and blocks all Sprint 1 work. The `EXCLUDE USING gist` constraint on `customer_segment_assignment`, scoped to authoritative non-superseded rows, makes the incorrect pattern fail at the database level rather than pass silently, so a misunderstanding surfaces in minutes rather than in Sprint 2. It replaces the partial unique index originally specified and is strictly stronger: the index caught only a second *open* assignment, whereas the constraint also rejects two **closed intervals that overlap** — the more likely bug, and the one that produces history which looks plausible while being wrong (D-03).

**How this risk is monitored.** The 16 checks in `infra/sql/schema/verify_m1_schema.sql` are executable, and CHECKS 2, 3, 5, 6 and 7 test exactly the failure modes this risk describes. They now run in CI: the `Schema ERD` workflow applies `alembic upgrade head` against an empty PostgreSQL 16 and then runs `infra/sql/schema/assert_m1_verification.sh`, which asserts on the checks' output and fails the build if any of them stops behaving as its `expected:` line states.

The assertion is two-sided, which is what makes it a control rather than a formality. Nine of the sixteen checks pass by *raising* an error, so exit status proves nothing — a database that had lost every constraint would exit 0. The script instead asserts an exact error count and the specific constraint name each check must name. A constraint that stops rejecting lowers the count; a check that starts failing raises it. Both fail the build. Verified against two deliberately broken schemas before the control was adopted: dropping the exclusion constraint, and — the failure this risk actually fears — redeclaring it `INITIALLY DEFERRED`, which leaves the constraint present, correctly named, and silently no longer fail-fast.

With the automated control in place, probability is 3 rather than 4 and exposure is 15, below the escalation threshold. **The local requirement is unchanged**: the script still runs locally and its complete output is still attached to the S0-04a issue, because CI proves the schema is intact and the attached output is what an evaluator reads. CI supplements that evidence; it does not replace it.

**Trigger.** Not frozen by end of Tuesday 2026-08-11 → escalate at the Wednesday refinement and freeze the transactional subset separately. S0-04a exists as a separate story so this trigger is testable against something specific rather than against a five-part story that is partially done.

---

### R-02 — Product Owner unavailable (Exposure 12)
The Product Owner is the course professor. He does not attend planning or refinement and cannot answer scope questions same-day. Stories block waiting for decisions nobody is empowered to make.

**Mitigation.** Raquel holds delegated authority as Proxy Product Owner. Every unanswered question is recorded in the Assumption Register with the decision taken anyway and the cost of being wrong. Work never stops waiting for an answer. The Scrum Master sends one weekly written digest; silence for five working days is treated as confirmation.

**Residual.** An assumption may be wrong and cost rework. This is accepted, and the Assumption Register records the exposure so the cost is visible rather than surprising.

---

### R-03 — Scrum Master is triple-hatted (Exposure 16)
Marcelo holds full-stack development, DevOps, and Scrum Master. The Sprint 0 load calculation put him at more than double his available capacity before rebalancing, which confirms the risk is live rather than theoretical.

**Mitigation.** Documented backups: Max for DevOps and infrastructure, Raquel for facilitation. Sprint planning includes a per-person load check against individual capacity, and any person above capacity triggers reassignment before commitment. Sprint 0 already reassigned ADR-003 to Max and engineering standards to Raquel on this basis.

**Trigger.** Marcelo's assigned points exceed his individual capacity at any planning session → reassign before committing, not during the sprint.

---

### R-04 — Seed dataset inadequate (Exposure 15)
RFM over a small hand-entered dataset produces degenerate quintiles, and with no purchase history there is nothing for a migration report to detect. The Sprint 2 demonstration — the part that distinguishes this project from a CRUD application — becomes impossible.

**Mitigation.** Start from a public retail dataset with genuine purchase behaviour rather than fully synthetic generation. Layer store, channel and controlled migrations on top. Record the injected migration ground truth so detection is validated against known answers rather than judged by eye.

**The ground truth must be expressed in segment labels, not cluster indices.** Cluster indices are arbitrary and unstable between runs (D-04), so a ground truth recorded as "customer 417 moves from cluster 2 to cluster 5" cannot be compared against detection output at all — the detector emits label codes, and the two runs' cluster numbering has no relationship to each other. Record the expected movement as `champions → at_risk`, and the comparison becomes a join. This depends on A-06 in `assumption-register.md`, which fixes the label set, being answered before S0-08 generates the data.

**Trigger.** Fewer than 2,000 customers or fewer than 18 months of history after loading → raise immediately; this is a Sprint 2 blocker discovered in Sprint 0.

---

### R-05 — Seven-profile scope creep (Exposure 12)
The specification names seven profiles. Building seven differentiated experiences inside 212 person-hours leaves no capacity for the segmentation pipeline.

**Mitigation.** All seven roles exist in the database with a complete documented permission matrix. Three receive real screens in M1: Administrator, Commercial Analyst, Auditor. The remaining four authenticate, see a correct menu, and land on a placeholder. This is recorded as a deliberate scope decision in the Assumption Register, not left implicit, so it can be defended at review.

---

### R-06 — Duplicated authentication (Exposure 12)
JWT is required, but the web system is server-rendered with Jinja2. The common failure is building Flask session cookies for the web and JWT for the API clients, producing two revocation paths that can disagree — a user "logged out" of one and not the other.

**Mitigation.** ADR-001 fixes one JWT scheme with two transports: `HttpOnly` cookie for the web, `Authorization` header for mobile and desktop, with a single Redis-backed revocation denylist serving both. The ADR is written in Sprint 0 and reviewed by a second person before any implementation.

---

### R-07 — XML retrofitted late (Exposure 12)
The desktop application consumes XML exclusively. If the microservices are built JSON-only in M2, adding XML in M3 means rewriting the serialization layer across every endpoint.

**Mitigation.** ADR-003 specifies `Accept`-header content negotiation, the JSON-to-XML mapping conventions, and a format-agnostic error envelope in Sprint 0. Implemented once in the M2 platform foundation. Owned by Max, who will implement it.

---

### R-08 — Fabricated board history (Exposure 8)
"Tablero de tareas" and "Registro de incidencias" are graded deliverables. A board where forty issues close on 2026-09-07 is visibly reconstructed and is worth less than a board showing steady progress with honest spillover.

**Mitigation.** Board discipline is a working agreement: issues move to In Progress the same day work starts, and nothing is closed retroactively in bulk. Documentation lives in Git so the commit history is independent evidence of incremental work. Spillover is recorded in retrospectives rather than hidden.

---

### R-09 — GCP problems surface late (Exposure 12)
Billing not enabled, APIs not enabled, quota limits, or missing IAM permissions typically appear when someone first tries to deploy — in Sprint 2, with four days left.

**Mitigation.** Max verifies billing status, enables required APIs, and confirms quota on Day 1 of Sprint 0, even though deployment is Sprint 2. A trivial "hello world" container is deployed to Compute Engine in Sprint 0 as a smoke test of the whole path.

---

### R-10 — Demonstration fails live (Exposure 15)
Programming until the delivery date and demonstrating without rehearsal is the most common cause of a failed milestone, and it costs more marks than a missing feature.

**Mitigation.** A three-day hardening period from 2026-09-05 with zero new stories. Two full rehearsals from a clean clone, at least one on a machine that is not the primary developer's. A written demo script with a defined fallback for each step.

---

### R-11 — Other courses consume capacity (Exposure 12)
The plan assumes 15 hours per person per week. Students carry other courses with their own deadlines, and the assumption will be violated at some point.

**Mitigation.** A 0.80 focus factor is already applied, and commitment is held at roughly 84% of capacity. Per-person available capacity is reconfirmed at each planning session, including known absences. Weekend hours are recovery capacity, not planned capacity; two consecutive weeks of weekend recovery is treated as a signal that the commitment is too high, and the next sprint is reduced.

---

### R-12 — Privacy exposure (Exposure 8)
A consumer segmentation and personalization platform processes purchase history, location and behavioural profiles. Mexican data protection law (LFPDPPP) requires notice, purpose limitation and consent. The course explicitly asks for legal, ethical and privacy constraints as part of the analysis deliverable, so this is graded content and not only a real-world concern.

**Mitigation.** Consent capture at loyalty enrollment from M1. Explicit privacy controls in the mobile app (E-34). No real personal data used at any point — the seed dataset is public and anonymized, with the synthetic overlay documented. A privacy and ethics section in the problem analysis document covering purpose limitation, retention, and which automated decisions must remain subject to human review.

---

### R-13 — Image bloat and slow builds (Exposure 6)
pandas, scikit-learn and their transitive dependencies produce large images and slow rebuild cycles, which compounds across a four-person team rebuilding many times a day.

**Mitigation.** `python:3.12-slim` rather than Alpine, since musl wheels are unavailable for the scientific stack and source compilation would be far worse. Dependency installation in a separate layer above the source copy so code changes do not reinstall dependencies. Multi-stage build evaluated in Sprint 2 if build time exceeds three minutes.

---

### R-14 — Estimation optimism (Exposure 12)
The Sprint 0 story totals came out at 25 SP against a 19 SP capacity on the first attempt. Systematic over-commitment produces spillover, which produces schedule pressure, which produces skipped Definition of Done items.

**Mitigation.** Commitment held at roughly 84% of capacity, not 100%. Nobody estimates alone; no estimate is revised downward without discussion. Commitment reliability is tracked as a metric and reviewed each retrospective. Velocity replaces the bootstrap hours anchor after Sprint 1.

---

### R-15 — Premature Kubernetes adoption (Exposure 8)
"Online clustering" in the requirements describes an algorithm — incremental K-means over a transaction stream. It does not describe a container orchestrator. Reading it as a mandate for GKE would consume roughly a week on Ingress, Secrets, service accounts, manifests and network debugging, and would deliver nothing the M1 rubric asks for, since M1 explicitly requires local `docker-compose` and Compute Engine.

**Mitigation.** ADR-004 records the decision to defer GKE and states the condition under which it would be revisited: a demonstrated need for the incremental segmentation and drift services to scale independently of the rest of the platform. Evaluated at the end of M2, not before.

---

### R-16 — M2 and M3 deadlines unknown (Exposure 9)
The M1 architectural contracts are sized on the assumption that M2 begins immediately after M1. If there is a substantial gap, or if M2 and M3 are compressed together, the sequencing changes.

**Mitigation.** Confirm both deadlines with the Product Owner before Sprint 2 planning. Recorded in the Assumption Register until confirmed.

---

### R-17 — Non-deterministic segment labelling reports noise as migration (Exposure 15)
K-means cluster indices are arbitrary and unstable between runs: the cluster numbered 2 in July and the cluster numbered 2 in January have no relationship to each other. If the mapping from cluster to segment label is decided by a human looking at centroids after each run, then two runs over a customer whose behaviour did not change can still produce two different labels — and the migration report presents that as a behaviour change.

This is the failure that survives every other control. The schema is correct, the constraint holds, the pipeline runs, the report renders, and the number is wrong. It is also the failure that is hardest to notice from the inside, because a migration report with plausible-looking movement is exactly what the demonstration is supposed to produce.

**Mitigation.** Labelling is a **rule over centroid position**, recorded in `segmentation_model_run.labelling_strategy` and reapplied identically on every run. It is never assigned by hand between runs. The rule is part of the run's reproducibility set alongside `random_seed`, `code_version` and `scaler_state`: a run whose labelling cannot be reproduced cannot be defended at review.

**Trigger.** Two runs over the same feature set produce different label assignments for a customer whose features did not change.

**Note on ownership.** This is Estefanía's, and it is work that no story currently points at — the labelling rule is recorded in `03-sprint-00-backlog.md` as unestimated new work on a person already at 1.88× individual capacity. The risk is live because the mitigation is unfunded, not because the mitigation is unclear.

---

### R-18 — Application connects as schema owner, making the append-only audit trail decorative (Exposure 12)
`audit_log` is append-only, enforced by a statement trigger *and* by `REVOKE`. A `REVOKE` has no effect against the role that owns the schema, and PostgreSQL superusers and table owners bypass row-level controls by design. If the application connects with the same credentials that ran the migration, the second layer of enforcement does nothing, and the audit trail is protected only by a trigger that the owning role can drop.

The audit trail is a graded deliverable and the Auditor profile is the one that demonstrates the traceability requirement. An audit trail that the application can rewrite is not an audit trail.

**Mitigation.** Two database roles and two DSNs: `DATABASE_URL` for the restricted runtime role, `DATABASE_MIGRATION_URL` for the schema owner. Plus **a test asserting that the runtime role cannot `UPDATE` `audit_log`** — the assertion is what makes this a control rather than an intention, because the two-role setup is easy to configure and easy to silently undo when someone debugs a permissions error at 2am by widening a grant.

**Trigger.** `.env.example` contains one DSN at the end of Sprint 0.

---

## Closed risks

None yet.

---

## Review log

| Date | Reviewed at | Changes |
|---|---|---|
| 2026-08-09 | Sprint 0 readiness audit | R-01 reclassified to monitored-by-CI, this time against a job that exists. `assert_m1_verification.sh` runs the 16 checks in the `Schema ERD` workflow and was validated against two deliberately broken schemas before adoption. Probability 4 → 3, exposure 20 → 15, matrix updated, escalation no longer required. The local-evidence requirement on the S0-04a issue is unchanged. **R-01 has now been reclassified three times in two days**; the register should stop moving it until either the control fails or Sprint 1 closes. |
| 2026-08-09 | Sprint 0 planning correction | R-01 returned to monitored-by-review because no CI job executes `verify_m1_schema.sql`. Probability increased from 3 to 4, exposure increased from 15 to 20, the matrix was updated, and Product Owner escalation is required. |
| 2026-08-08 | Initial | Register created with 16 risks |
| 2026-08-08 | S0-04 data model review | R-01 mitigation updated to the exclusion constraint and reclassified from monitored-by-review to monitored-by-CI. R-04 mitigation now requires the injected ground truth to be expressed in segment labels. R-17 and R-18 added. Exposure matrix updated; the probability 3, impact 5 cluster grows from three risks to four. Register now holds 18 risks. |

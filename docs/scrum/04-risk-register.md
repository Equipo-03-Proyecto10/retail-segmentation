# Risk Register

**Project:** Dynamic Segmentation and Retail Personalization Platform
**Owner:** Marcelo (Scrum Master)
**Version:** 1.0
**Date:** 2026-08-08
**Review cadence:** Every retrospective

Scoring: Probability (1–5) × Impact (1–5) = Exposure. Exposure ≥ 12 requires an owned mitigation with a due date. Exposure ≥ 20 is escalated to the Product Owner.

---

## Exposure matrix

Probability down the side, impact across. Cells with several entries are where the project is actually exposed.

| P ↓ / I → | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| **5** | | | | | |
| **4** | | | R-02 R-05 R-11 R-14 | R-03 | |
| **3** | | R-13 | R-16 | R-06 R-07 R-09 | R-01 R-04 R-10 |
| **2** | | | | R-08 R-12 R-15 | |
| **1** | | | | | |

Bands: exposure ≥ 12 requires an owned mitigation with a due date, 8–11 is monitored at each retrospective, ≤ 6 is accepted.

Two clusters matter. The four risks at probability 4, impact 3 are all the same underlying problem — the team commits more than it can deliver, whether the cause is an absent Product Owner, seven specified profiles, competing coursework, or optimistic estimation. They are tracked separately because they need separate mitigations, but if commitment discipline holds, all four drop at once. The three at probability 3, impact 5 are the demonstration risks: no frozen schema, no realistic data, no rehearsal. Each one alone is enough to fail the milestone demo.

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

---

## Detail and mitigation

### R-01 — Segment model not frozen before development (Exposure 15)
Sprint 1 stories that read or write segment data cannot be estimated until the assignment model is decided, and code written against a mutable `customer.segment_id` must be rewritten once traceability is introduced.

**Mitigation.** S0-04 is timeboxed to two days and blocks all Sprint 1 work. The partial unique index on `customer_segment_assignment` where `valid_to IS NULL` makes the incorrect pattern fail at the database level rather than pass silently, so a misunderstanding surfaces in minutes rather than in Sprint 2.

**Trigger.** Not frozen by end of Tuesday 2026-08-11 → escalate at the Wednesday refinement and freeze the transactional subset separately.

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

## Closed risks

None yet.

---

## Review log

| Date | Reviewed at | Changes |
|---|---|---|
| 2026-08-08 | Initial | Register created with 16 risks |

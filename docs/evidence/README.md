# Evidence

Every document here records a result that was actually observed: a command and
its output, a screenshot with its caption, a refusal reproduced on purpose.
Deliverables 7, 10, 11 and 13 in [`../scope.md`](../scope.md) §5 are satisfied
from this directory.

An item on the [traceability table](../requirements.md) §4 with no document
here is a gap, not an omission from this index.

## Phase 1 — the instance and PostgreSQL

| Document | What it records |
|---|---|
| [F1-04 / F1-05](f1-04-f1-05-postgresql-access.md) | Live database access, HBA rejection checks and least-privilege acceptance |

## Phase 2 — the model

| Document | What it records |
|---|---|
| [F2-07](f2-07-integrity-verification.md) | **Deliverable 7.** The schema constraints refusing the writes they exist to refuse |

## Phase 3 — the application

| Document | What it records |
|---|---|
| [F3-05](f3-05-consultation-module.md) | The read-only consultation module: products, customers, stock, segment membership |
| [F3-10](f3-10-segment-run.md) | The segment recalculation over a configurable window |
| [F3-11](f3-11-audit-log-view.md) | The audit log read from the application |
| [F3-12](f3-12-application-shell.md) | The authenticated shell and its navigation |

## Phase 4 — roles and permissions

| Document | What it records |
|---|---|
| [F4-01](f4-01-authorization.md) | Route-level authorization against the permission matrix |
| [F4-02](f4-02-single-administrator.md) | The single-administrator rule refused twice — application and partial unique index |
| [F4-06](f4-06-real-administrator.md) | An administrator provisioned on the instance without a published password |

## Phase 5 — tests and review

| Document | What it records |
|---|---|
| [F5-01](f5-01-functional-tests.md) | Functional tests over login, CRUD and image upload |
| [F5-02](f5-02-negative-tests.md) | **Deliverable 11.** Access refusals, invalid submissions and controlled failures |
| [F5-03](f5-03-code-review.md) | The whole-codebase review pass and the issues it opened |
| [F5-04](f5-04-key-functionality.md) | **Deliverable 10.** A captioned screenshot for every demonstration item |

## Phase 6 — deployment

| Document | What it records |
|---|---|
| [F6-06](f6-06-continuous-deployment.md) | The deploy pipeline, the rollback test, and the four failures before it |
| [F6-05](f6-05-final-verification.md) | **Deliverable 14, and a gap in it.** The published site checked with no session, and the documentation that is not reaching it |
| [F6-07](f6-07-proof-of-deployment.md) | **Deliverable 13.** The unit active and enabled, restarting after a kill, answering through the reverse proxy, and the same application under Compose |

All five acceptance criteria on #109 are recorded. Four were captured against
the instance and the published host; container execution was captured on a
developer machine, which
[ADR-0015](../adr/0015-containers-are-a-development-path-only.md) settles as the
right place for it, and which the team accepted on 2026-09-07.

## Review passes

These follow a specific review rather than a story.

| Document | What it records |
|---|---|
| [Instance findings](instance-findings-fixes.md) | The 17 findings of the 2026-09-06 browser review, and their disposition |
| [Navigation and upload hotfix](navigation-upload-hotfix.md) | The integration review of PR #132 |
| [F5-03 resolution](review-findings-resolution.md) | Resolution of #157–#165, and the ADR acceptance basis |

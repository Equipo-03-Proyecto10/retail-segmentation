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
| [F6-05](f6-05-final-verification.md) | **Deliverable 14.** The published site checked with no session; two faults that kept the documentation off it, both fixed and re-verified |
| [F6-07](f6-07-proof-of-deployment.md) | **Deliverable 13.** The unit active and enabled, restarting after a kill, answering through the reverse proxy, and the same application under Compose |

All five acceptance criteria on #109 are recorded. Four were captured against
the instance and the published host; container execution was captured on a
developer machine, which
[ADR-0015](../adr/0015-containers-are-a-development-path-only.md) settles as the
right place for it, and which the team accepted on 2026-09-07.

## Phase 8 — sales ingestion and consumption profile

| Document | What it records |
|---|---|
| [F8-03](f8-03-consumption-profile.md) | The consumption profile computed over accepted sales, cross-checked against independent queries on the seeded database, with its ties, empty case and single-assignment case reproduced |
| [F8-04](f8-04-consumption-profile-view.md) | The consumption profile page at 375 px and 1440 px: every measure with its unit and window, the no-purchase-history case, and the refusal of a role without `segment.read` |
| [F8-05](f8-05-consumption-shifts.md) | Channel, store and category shifts between two stated periods, cross-checked against an independent recomputation on the seeded database, with absence, ties and the shared boundary instant reproduced |

## Phase 9 — segmentation modelling

| Document | What it records |
|---|---|
| [F9-01](f9-01-rfm-rules-adapter.md) | The rule-based scoring as an adapter behind a method-agnostic pipeline: the new writes compared with the old single statement on the seeded database, the method domain enforced by the database, and a consumer that is never told the method |
| [F9-02](f9-02-kmeans-fit.md) | The K-means fit written in the application: checked against exact rational arithmetic and scikit-learn, its empty-cluster, non-convergence and tie behaviours on real rows, and the parameters and quality measures a run records |
| [F9-03](f9-03-cluster-labels.md) | K-means clusters mapped to the stable labels by ADR-0018's deterministic order: checked against the stored rows, 500 renamings of a real partition, a refused k, and two runs that number the same partition differently |
| [F9-04](f9-04-model-comparison.md) | The model comparison page at 375 px and 1440 px: per-label populations and per-customer agreement checked against independent SQL, a run whose labels are copied giving 100% agreement whatever its method, the empty and error states, and the refusal of a role without `segment.read` |

## Phase 10 — recommendations

| Document | What it records |
|---|---|
| [F10-01](f10-01-recommendations.md) | Product recommendations with a stated reason for each: the recommendations of all 30 seeded customers checked against an independently written query, stock in the usual store enforced, no segment and no usual store stated instead of substituted, and only the stable label read |
| [F10-02](f10-02-customer-recommendations.md) | The customer recommendations page at 375 px and 1440 px: product, store, stock and reason on each recommendation, a product leaving the list when its stock reaches zero and the page is reloaded, the explained empty states, and the refusal of a role without `segment.read` |
| [Category subtree](recommendations-category-subtree.md) | #277: a preferred or bought category covers the categories below it, checked against an independent recursive query on all 30 seeded customers and shown on a subcategory product stocked in a rolled-back transaction |
| [Store managers read segments](store-manager-segment-read.md) | #280 and ADR-0023: `STORE_MANAGER` holds `segment.read`, reaching every customer and segment surface (recommendations at 375 px and 1440 px) and still refused every write, while `INVENTORY_PLANNER` stays out |

## Phase 11 — campaigns and experiments

| Document | What it records |
|---|---|
| [F11-03](f11-03-experiment-setup.md) | Experiment setup on a real database: one control and the treatment groups written together, the conversion window and target metric locked after the first assignment, a campaign refused activation while an attached experiment lacks a group, the data origin fixed and labelled `Synthetic`, at 375 px and 1440 px |
| [F11-04](f11-04-group-assignment.md) | Group assignment as `retail_app` on a real database: the campaign's population split into balanced arms in one transaction, a second attempt refused, `UPDATE` and `DELETE` refused by the schema (42501) as well as by the application, the preview and a refusal at 375 px and 1440 px |

## Phase 12 — analytics

| Document | What it records |
|---|---|
| [F12-01](f12-01-segmentation-dashboard.md) | The segmentation dashboard: segment sizes, RFM distribution, migration flow and revenue by label for one run, checked against independent SQL and a real browser with no external network requests, Highcharts vendored rather than loaded from a CDN, and the refusal of a role without `segment.read` |

## Review passes

These follow a specific review rather than a story.

| Document | What it records |
|---|---|
| [Instance findings](instance-findings-fixes.md) | The 17 findings of the 2026-09-06 browser review, and their disposition |
| [Navigation and upload hotfix](navigation-upload-hotfix.md) | The integration review of PR #132 |
| [F5-03 resolution](review-findings-resolution.md) | Resolution of #157–#165, and the ADR acceptance basis |
| [Catalog label reads](catalog-label-reads.md) | #272, found reviewing F9-03: the customer pages read the label, so a K-means assignment is no longer shown as *Unassigned*, before and after on a real K-means run at 375 px and 1440 px |

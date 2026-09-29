# F11-04 — Group assignment as a durable event

Issue #223. Assignment is the first of an experiment's three events
([ADR-0019](../adr/0019-experiment-measurement-separates-assignment-exposure-and-conversion.md)).
It is written with its timestamp before anything is delivered, and is never
rewritten ([ADR-0026](../adr/0026-experiment-assignments-are-append-only-for-the-application-role.md),
RN-42).

## How this run was produced

PostgreSQL 16.2 loaded from empty with the three scripts in `sql/`, on the
branch `feature/223-experiment-assignment`. Every step below ran **connected as
`retail_app`**, the application's own role, through the service layer, and the
screens were captured through gunicorn in Chromium.

## The schema refuses the application role a rewrite

The verification section of `sql/01_schema.sql`, run as `retail_app` as CI does:

```
NOTICE:  PASS: restricted role, no ownership or CREATE, DML on all tables
NOTICE:  PASS: audit_log is append-only for the application role
NOTICE:  PASS: experiment_assignment is append-only for the application role
NOTICE:  PASS: DROP TABLE inventory refused (SQLSTATE 42501)
NOTICE:  PASS: UPDATE audit_log refused (SQLSTATE 42501)
NOTICE:  PASS: DELETE audit_log refused (SQLSTATE 42501)
NOTICE:  PASS: UPDATE experiment_assignment refused (SQLSTATE 42501)
NOTICE:  PASS: DELETE experiment_assignment refused (SQLSTATE 42501)
NOTICE:  PASS: SELECT, INSERT, UPDATE, DELETE and audit sequence access
```

## One run, end to end

```
connected as: retail_app
campaign 1 targets CHAMPION; activate: ACTIVE
preview for experiment 31: [('CONTROL', 2), ('TREATMENT', 2), ('TREATMENT', 1)] written so far: 0
assigned: [('CONTROL', 2), ('TREATMENT', 2), ('TREATMENT', 1)]
rows, distinct assigned_at: (5, 1)
second attempt: Experiment 31 already has 5 assignments. Its arms were fixed when they were written (ADR-0019).
UPDATE as retail_app: 42501 permission denied for table experiment_assignment
DELETE as retail_app: 42501 permission denied for table experiment_assignment
window edit after assignment: The conversion window is fixed: experiment 31 already has 5 assignments (ADR-0019).
assign on a draft campaign: Campaign 5 is draft. Customers are assigned once it is active, when its target label can no longer change.
```

## Criterion 1 — each assignment is written with its timestamp before any exposure or conversion

- **Who is assigned.** The customers whose open segment assignment carries the
  campaign's target label at that moment. That is five customers for `CHAMPION`
  in the seed, which is why the arms are small.
- **When.** Only once the campaign is `ACTIVE`, because while it is a draft its
  target label can still change.
- **How the arms are split.** Customers are shuffled by a generator seeded on
  the experiment id, so the split is reproducible when audited, and then dealt
  round-robin, control first, so arms differ by at most one.
- **The write.** Every row is written in one statement batch and carries the
  same `assigned_at`. Exposure and conversion both reference an assignment, so
  neither can exist before it.

The preview writes nothing ("written so far: 0" above) and shows exactly what
will be written:

| Width | Capture |
|---|---|
| 1440 px | [`f11-04-assign-preview-1440.png`](f11-04-assign-preview-1440.png) |
| 375 px | [`f11-04-assign-preview-375.png`](f11-04-assign-preview-375.png) |

Confirming in the browser redirected to the list with *"Experiment 33: 5
customers assigned (control 3, treatment 2)."*

## Criterion 2 — a second assignment is refused by the database

`UNIQUE (experiment_id, customer_id)` refuses it (RN-23, case N21 of
`sql/verify_integrity.sql`). Before the insert is reached, the application:

- takes `FOR UPDATE` on the experiment row;
- refuses an experiment that already has assignments ("second attempt" above);
- reports the index's own refusal as a message, never a 500
  (`test_the_databases_refusal_of_a_second_assignment_is_reported`).

## Criterion 3 — a failure part way leaves no partial arm

The whole assignment is one transaction (`@atomic`). A failure during the insert
rolls it back and commits nothing
(`test_a_failure_part_way_rolls_the_whole_assignment_back`).

## Criterion 4 — an assignment cannot be edited or deleted

Enforced in two places:
- **Application.** No module under `web/` updates or deletes an assignment
  (`test_no_module_updates_or_deletes_an_assignment`).
- **Schema.** `retail_app` has no `UPDATE` or `DELETE` on the table, shown
  above twice: by the self-test and by the direct statements (42501).

## Refusals

| Width | Capture |
|---|---|
| 375 px | [`f11-04-assign-refused-375.png`](f11-04-assign-refused-375.png): a draft campaign, 409 with the reason |

## Widths

Every capture was taken with no horizontal scroll (`scrollWidth − clientWidth = 0`).

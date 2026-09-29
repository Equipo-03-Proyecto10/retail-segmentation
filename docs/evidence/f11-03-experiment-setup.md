# F11-03 — Experiment setup

Issue #222. An experiment's measurement rules are fixed before its first
assignment ([ADR-0019](../adr/0019-experiment-measurement-separates-assignment-exposure-and-conversion.md)):
exactly one control group, at least one treatment group, a target metric, a
conversion window and a data origin.

## How this run was produced

PostgreSQL 16.2 loaded from empty with the three scripts in `sql/`, on the
branch `feature/222-experiment-setup`. The first part called the service layer
(`web.services.experiments`, `web.services.campaigns`) on a real connection. The
screens were captured through gunicorn in Chromium, signed in as the seeded
administrator. Every seeded experiment already has four assignments, so each
one shows the locked form.

## Criterion 1 — setup requires one control, a treatment, a metric, a window and an origin

The form asks for all of them. Validation refuses a missing or invalid value
per field (`tests/test_experiments.py::test_setup_refuses_a_missing_or_invalid_field`).
The control group is not a choice: creation always writes exactly one, with
the treatment groups, in the same transaction.

```
created experiment 31 groups: [('CONTROL', 1), ('TREATMENT', 2)]
audit rows for it: 1
resubmitted (campaign NULL): An experiment with this name, campaign and start date already exists.
```

| Width | Capture |
|---|---|
| 375 px | [`f11-03-new-375.png`](f11-03-new-375.png) |

## Criterion 2 — the conversion window cannot change after the first assignment

One assignment recorded directly, since F11-04 has not built assignment yet.
The window change is then refused, while a rename still goes through:

```
window change after assignment: The conversion window is fixed: experiment 31 already has 1 assignment (ADR-0019).
window in DB: (14, 'INJECTED')
rename after assignment: Live check renamed
```

The target metric is locked the same way: changing what is measured after
the outcome is visible is the same tuning the window lock prevents. The edit
takes `FOR UPDATE` on the experiment row before it counts assignments. F11-04
must take at least `FOR SHARE` on that row before an experiment's first
assignment, so the two cannot pass their checks concurrently.

| Width | Capture |
|---|---|
| 1440 px | [`f11-03-edit-locked-1440.png`](f11-03-edit-locked-1440.png): the rules shown read-only, with the notice |

## Criterion 3 — activation without a control group is refused

An experiment has no status of its own. It starts with its campaign, so the
check runs when the campaign is activated: every experiment attached must
have one control group and at least one treatment group (RN-24). The
database already refuses a second control; this is the half a constraint
cannot express. Here, campaign 1's experiment lost its treatment group through
direct SQL:

```
activate campaign with a treatment-less experiment: Campaign 1 cannot be activated: experiment 1 (Experiment 1) has no treatment group.
its status: DRAFT
activate a complete one: ACTIVE
```

A missing control group is refused the same way
(`test_an_incomplete_experiment_blocks_its_campaigns_activation`).

## Criterion 4 — the data origin is stored and carried

The origin is chosen at creation and never updated: the `UPDATE` statement
does not name the column (`test_the_data_origin_is_never_rewritten`), and the
edit form shows it read-only. `SEEDED` and `INJECTED` experiments carry the
`Synthetic` label on every row and form that shows them. F11-07's results
read the origin from this same row.

| Width | Capture |
|---|---|
| 1440 px | [`f11-03-experiments-1440.png`](f11-03-experiments-1440.png) |
| 375 px | [`f11-03-experiments-375.png`](f11-03-experiments-375.png) |

## Criterion 5 — the default-deny gate refuses a profile without the permission

The list needs `campaign.read`, and setup and edit need `campaign.write`, as
`docs/analytics-permission-map.md` assigns to experiments. `ANALYST`,
`STORE_MANAGER` and `AUDITOR` get 403 on setup. `INVENTORY_PLANNER` and
`CUSTOMER` get 403 on the list as well. All four routes are in the refusal
matrix of `tests/test_negative_flows.py`.

## Widths

Both widths have no horizontal scroll (`scrollWidth − clientWidth = 0`) on
every capture.

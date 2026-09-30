# #350 — segment history, runs and experiments protected from the application role

Run on a real PostgreSQL 16 (Docker): the three scripts loaded clean in order from this
branch, the application role connecting as `retail_app`.

## Load and self-test

- `00`, `01` and `02` ran with `ON_ERROR_STOP`: `btree_gist` was created, and the
  exclusion constraint accepted the 900 seeded history rows.
- The opt-in application-role self-test (`-v verify_app_role=1`, connected as
  `retail_app`) passed every check, including the new "history, runs and experiments are
  protected from the application role".

## `sql/verify_integrity.sql` cases

| Case | Result |
|---|---|
| N32 score of 9 | `customer_segment_history_r_score_check` |
| N33 overlapping interval | `ex_customer_segment_history_no_overlap` (23P01) |
| N34 rewriting a closed row | "Segment history row 1 may only be closed, once" |
| N35 changing a label | the same trigger, on an open row |

N32 first failed for the wrong reason: it was an `UPDATE`, and the close-only trigger
refused it before the `CHECK` could. It is now an `INSERT` into a new run, and fails on
the constraint the case names.

## As `retail_app`

Refused with "permission denied": `DELETE` on `experiment` and `experiment_group`;
`UPDATE` and `DELETE` on `segmentation_run`; `UPDATE` of `label_code` or `segment_id`
and `DELETE` on `customer_segment_history`. Allowed: setting `valid_to` on an open row,
and renaming an experiment. Closing an already closed row is refused by the trigger.

## The pipeline still works

An `RFM_RULES` and a `KMEANS` run through `run` and `run_kmeans`, as `retail_app`, both
succeeded and closed the previous rows: 30 open rows, no customer with two open rows, no
overlapping interval among 960 rows. The `RFM_RULES` run assigned every label with the
fallback used for nobody (AT_RISK 1, HIBERNATING 9), which also confirms the seed bands
of #345 on a real database.

## Not done

The instance database does not have any of this until it is rebuilt (#346, #360).

# ADR-0029 — Segment history, runs and experiments are protected from the application role

**Status:** Accepted
**Owner:** Max
**Issue:** #350
**Supersedes:** —
**Superseded by:** —

---

## Context

ADR-0017 says a segment assignment is never updated in place, and ADR-0026 and
ADR-0027 make experiment assignments and exposures append-only for `retail_app`.
A QA round found the protection incomplete. On a clean build `retail_app` still
held `UPDATE` and `DELETE` on `customer_segment_history` and `segmentation_run`,
and `DELETE` on `experiment` and `experiment_group`. Foreign-key cascades run as
the owner, so `DELETE FROM experiment` removed the assignments, exposures and
conversions that the earlier `REVOKE`s protect, and deleting a run removed every
assignment that named it. `customer_segment_history` also had no bound on its
scores or measures and nothing stopping one customer's intervals overlapping.

The application needs very little of this: it inserts runs, inserts history rows
and sets `valid_to` on the open one (`web/db/segments.py`), and it edits an
experiment before its first assignment (`web/db/experiments.py`). It never deletes
an experiment, a group, a run or a history row.

## Decision

- `retail_app` loses `DELETE` on `experiment` and `experiment_group`, and both
  `UPDATE` and `DELETE` on `segmentation_run`. It keeps `UPDATE` on `experiment`.
- On `customer_segment_history` it loses `UPDATE` and `DELETE` and is granted
  `UPDATE (valid_to)` only. A trigger applies the same rule to every role: a row
  may be closed once, and no other column may change. The one exception is
  `segment_id` becoming `NULL`, which is the foreign key's own
  `ON DELETE SET NULL (segment_id)` retiring a band reference.
- The table gains `CHECK`s on the scores (1 to 5), the frequency and the total, and
  an exclusion constraint so one customer's intervals cannot overlap. It needs the
  `btree_gist` extension, created in `sql/00_create_database.sql`.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Change the experiment foreign keys from `CASCADE` to `RESTRICT` | Stops the owner as well, so a deliberate maintenance delete needs several steps, and it does nothing about the application role's own `DELETE` |
| Leave `UPDATE` on history and rely on the service | Anything holding the application credential could rewrite an assignment after the fact, which is the case ADR-0017 exists to prevent |
| Table-wide `REVOKE UPDATE` on history and close rows another way | Closing is the one legitimate update, and a second mechanism for it would be new code to change |
| Skip the exclusion constraint | The open-row index guards only the present; a past overlap would make "which segment on this date" ambiguous |

## Consequences

**What this makes easy.** The application connection cannot delete a run, an
experiment or a history row, or change what a row says, only close it. The rule is
visible in the schema, not only in the service.

**What this makes hard.** Correcting a mistaken run or removing a test experiment
needs the schema owner. A grant mistake would surface only as a runtime failure of
a segmentation run, so a run as `retail_app` is part of accepting this ADR. The
exclusion constraint needs `btree_gist`, one more extension for the database owner
to create.

**What must now be true elsewhere.** The application-role self-test names the four
tables as exceptions to the ordinary all-DML policy. `docs/business-rules.md` and
`docs/data-model.md` state the rules. The instance database has to be rebuilt from
the three scripts to receive any of this (#346, #360).

## Compliance

`tests/test_history_protection.py` checks the statements, their order and that the
pipeline still sets only `valid_to`. The opt-in application-role self-test in
`sql/01_schema.sql` checks the privileges, and cases N32 to N35 of
`sql/verify_integrity.sql` exercise the checks, the overlap and the trigger as the
owner. CI loads the three scripts and runs the self-test.

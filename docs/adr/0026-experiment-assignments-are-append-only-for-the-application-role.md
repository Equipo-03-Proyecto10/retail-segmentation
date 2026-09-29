# ADR-0026 — Experiment assignments are append-only for the application role

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #223
**Supersedes:** —
**Superseded by:** —

---

## Context

ADR-0019 makes group assignment the first of an experiment's three events,
written before anything is delivered, so that the denominator is fixed before
the outcome can influence it. F11-04's acceptance criteria add that an
assignment "cannot be edited or deleted to change the arm after the fact".

The application can simply offer no way to do that. But `retail_app` holds
`UPDATE` and `DELETE` on every table by default (`sql/00_create_database.sql`).
Anything holding the application's connection could therefore move a customer
from treatment to control, or drop the customers who did not convert, after the
result is visible. That is exactly the tuning ADR-0019 exists to prevent.
ADR-0024 settled the same question for `audit_log`: an application check alone
is bypassed by a direct statement.

This is the judgement call: the issue says "Schema change: no", written before
this was weighed. The change is one `REVOKE`, and no table's shape changes.

## Decision

`retail_app` loses `UPDATE` and `DELETE` on `experiment_assignment`, through a
`REVOKE` placed right after the table in `sql/01_schema.sql`. It keeps `SELECT`
and `INSERT`. The application also offers no path that updates or deletes an
assignment, so the rule is enforced in both places, as for the single
administrator and the audit log.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Application-only enforcement, keeping the issue's "no schema change" | A direct statement over the application's connection bypasses it, the case ADR-0024 already rejected for the audit log. |
| A trigger that raises on `UPDATE` or `DELETE` of `experiment_assignment` | It would also block the foreign-key cascades from `experiment_group` and the owner's own maintenance, and it is a larger schema change for the same protection against `retail_app`. |
| Make an arm change an audited event instead of forbidding it | An audited rewrite is still a rewrite of the denominator; the measurement would depend on reading the audit log to undo it. |

## Consequences

**What this makes easy.** Once written, the experiment's arms cannot be
rewritten by anything that only holds the application's connection. A refusal
reaches PostgreSQL as `insufficient_privilege`, which the self-test can check.

**What this makes hard.** Correcting a wrong assignment needs the owner role,
deliberately. A row lock (`FOR UPDATE`, `FOR SHARE`) on `experiment_assignment`
needs `UPDATE`, so application code serializes on the `experiment` row instead,
as F11-04's assignment does. The schema scripts run only on an empty database,
so the instance picks up the `REVOKE` when it is rebuilt from them at the next
release.

**What must now be true elsewhere.** The self-test in `sql/01_schema.sql`
excepts `experiment_assignment` from the all-DML check, next to `audit_log`,
and checks it separately. Foreign-key cascades run as the table owner, so
deleting an experiment group still cascades, as it did before. RN-42 in
`docs/business-rules.md` records the rule.

## Compliance

The privilege self-test in `sql/01_schema.sql` fails unless
`experiment_assignment` has `SELECT` and `INSERT` only. It then attempts an
`UPDATE` and a `DELETE` and expects `insufficient_privilege`; CI runs it as
`retail_app`. `tests/test_experiment_assignment.py` checks that the `REVOKE`
follows the table and that no module under `web/` updates or deletes an
assignment.

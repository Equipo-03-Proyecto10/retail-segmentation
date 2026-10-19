# ADR-0047 — Experiment conversions are append-only for the application role

**Status:** Proposed
**Owner:** Raquel
**Issue:** #451
**Supersedes:** —
**Superseded by:** —

---

## Context

ADR-0019 builds an experiment's measurement from three durable events:
assignment, exposure and conversion. ADR-0026 and ADR-0027 made the first two
append-only for `retail_app`, because anything holding the application
credential could otherwise move or drop events after the result is visible.
Conversions were left out. RN-27 says so: the application only ever adds
conversions, but `retail_app` still holds `UPDATE` and `DELETE` on
`experiment_conversion`, so the database does not enforce it. A direct
statement through that credential could delete one arm's conversions and change
the uplift (F11-07) with nothing refusing it.

The issue allows the other answer, re-evaluating conversions in place, if the
rules required it. They do not. Evaluating again adds only the sales not yet
recorded (`UNIQUE (assignment_id, transaction_id)`), and no web module issues
an `UPDATE` or `DELETE` on the table
(`tests/test_experiment_conversion.py::test_no_module_rewrites_a_conversion`).

## Decision

`retail_app` loses `UPDATE` and `DELETE` on `experiment_conversion`; it keeps
`SELECT` and `INSERT`. The schema owner keeps its maintenance privileges, and the
`ON DELETE CASCADE` from `experiment_assignment` still runs as the owner, which
`retail_app` cannot trigger anyway because assignments are append-only
(ADR-0026).

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Re-evaluate conversions in place, keeping `UPDATE` and `DELETE` | The rules never rewrite a conversion: evaluation only adds, and a recorded sale stays recorded. Keeping the privileges only keeps the bypass |
| Rely on the application never issuing the statements | ADR-0024, ADR-0026 and ADR-0027 already rejected an application-only guard: a direct statement through the same credential bypasses it |

## Consequences

**What this makes easy.** The whole measurement chain is now append-only for the
application credential. Once a conversion is recorded, nothing holding only
`retail_app` can rewrite or remove it.

**What this makes hard.** Correcting a wrongly recorded conversion needs the
schema owner. That is deliberate maintenance, not an application workflow.

**What must now be true elsewhere.**
- RN-27 and the `experiment_conversion` entry in `docs/data-model.md` state the
  enforcement.
- The application-role self-test in `sql/01_schema.sql` excepts the table from
  the all-DML policy, checks the narrower privileges, and attempts both
  rewrites.
- The instance database receives the revoke without a rebuild. It changes only a
  privilege, no table's shape, so it is the one idempotent statement
  `deploy/README.md` documents, run as `postgres`. A rebuild from the three
  scripts reaches the same state.

## Compliance

- `tests/test_experiment_conversion.py` checks that the revoke follows the table
  and that the self-test names the table and its three PASS lines.
- CI's "SQL scripts run clean from empty" job rebuilds PostgreSQL from the three
  scripts and runs the self-test as `retail_app` with `verify_app_role=1`. It
  must print `PASS: UPDATE experiment_conversion refused (SQLSTATE 42501)` and
  the same for `DELETE`.
- On the instance, `deploy/check-schema-drift.sh` reports no drift for the table.

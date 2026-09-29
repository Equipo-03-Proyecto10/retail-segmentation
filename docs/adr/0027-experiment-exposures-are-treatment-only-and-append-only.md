# ADR-0027 — Experiment exposures are treatment-only and append-only

**Status:** Proposed
**Owner:** Max
**Issue:** #224
**Supersedes:** —
**Superseded by:** —

---

## Context

ADR-0019 makes an exposure a durable event and requires the schema to reject
one for the control arm. F11-05 initially checked the group only in the service.
Anything holding the `retail_app` connection could bypass that check with a
direct `INSERT`; the same role's default `UPDATE` and `DELETE` privileges could
then move, backdate or remove an exposure after outcomes were visible. The
issue's “Schema change: no” was written before these two database-level gaps
were weighed.

The exposure relation deliberately stores only `assignment_id`, not group
kind. Duplicating `kind` onto every event would create two values that could
disagree and would weaken the existing 4NF decomposition.

## Decision

PostgreSQL follows every new or reassigned exposure through
`experiment_assignment` to `experiment_group` and raises SQLSTATE `23514` when
the group is `CONTROL`. The check is a trigger in `sql/01_schema.sql`, while the
service keeps its earlier check so a visitor receives a useful refusal instead
of a database error. `retail_app` also loses `UPDATE` and `DELETE` on
`experiment_exposure`; it retains `SELECT` and `INSERT`, and the schema owner
retains its maintenance privileges.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep only the service check and no schema change | A direct statement through the application connection can expose the control group, the exact case ADR-0019 says the schema rejects |
| Copy group kind onto `experiment_exposure` and add `CHECK (kind = 'TREATMENT')` | The event and its assignment could name different kinds; keeping one fact in one relation is the existing 4NF design |
| Permit `UPDATE` and `DELETE` because the web application exposes neither | Code reached through the same database account could still rewrite the delivery numerator after the outcome is known |

## Consequences

**What this makes easy.** Application code and direct SQL through `retail_app`
agree on the control refusal. Once inserted, an exposure cannot be rewritten by
anything holding only the application credential.

**What this makes hard.** Every exposure insert performs one indexed lookup of
its assignment and group. Correcting a mistaken event requires the schema owner;
that is deliberate maintenance rather than an application workflow.

**What must now be true elsewhere.** RN-27 and the data dictionary name both
enforcements. The application-role self-test excepts `experiment_exposure` from
the ordinary all-DML policy and verifies its narrower privileges. Integrity
verification includes direct insert and reassignment attempts for a control
assignment.

## Compliance

`tests/test_experiment_exposure.py` checks the trigger, privilege revocation and
application-side absence of rewrite statements. The opt-in application-role
self-test in `sql/01_schema.sql` attempts both rewrites and a control insert.
Cases N30 and N31 in `sql/verify_integrity.sql` exercise the trigger as the
schema owner. CI rebuilds PostgreSQL from the three ordered scripts before
running the application-role self-test.

# ADR-0024 — The application role cannot update or delete the audit log

**Status:** Proposed
**Owner:** Raquel
**Issue:** #290
**Supersedes:** — *(it replaces the reasoning recorded under RN-30, which was not an ADR)*
**Superseded by:** —

---

## Context

RN-30 says the audit log is append-only and recorded it as enforced by the
application only, "not constrained in the schema, because the role that owns
the schema must remain able to archive". The QA pass on 2026-09-28 showed the
consequence: connected as `retail_app` with the application's own
`DATABASE_URL`, `DELETE FROM audit_log` and `UPDATE audit_log` both succeed,
because `sql/00_create_database.sql` grants `SELECT, INSERT, UPDATE, DELETE` on
every table by default and nothing revokes it for `audit_log`.

The reason given does not hold. Archiving is done by the owner role, and
revoking privileges from `retail_app` does not touch the owner. Anything that
obtains the application's connection (SQL injection, a leaked `.env`, a bug)
can erase its own trail. AGENTS.md makes the same argument for the
single-administrator rule: an application check alone is bypassed by a direct
statement.

## Decision

`retail_app` loses `UPDATE` and `DELETE` on `audit_log`, through a `REVOKE`
placed right after the table is created in `sql/01_schema.sql`. It keeps
`SELECT` and `INSERT`; the audit trigger only inserts. The owner role is
unaffected, so archiving stays possible. The application check stays as well,
so the rule is enforced in both places.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep the application-only enforcement | A direct statement over the application's connection bypasses it, and that is the case the rule exists for. |
| A trigger that raises on `UPDATE` or `DELETE` of `audit_log` | It would also stop the owner role from archiving, which was the original reason for leaving the schema unconstrained. |
| A separate database role for writing audit entries | More moving parts and a second connection for no extra protection: the trigger writes with the invoker's privileges. |

## Consequences

**What this makes easy.** The audit trail cannot be rewritten by anything that
only holds the application's connection. A refusal reaches PostgreSQL as
`insufficient_privilege`, which the self-test can check.

**What this makes hard.** Removing entries (retention, a data-protection
request) now needs the owner role, deliberately. The schema scripts run only on
an empty database, so an existing instance needs the same `REVOKE` applied once
by the owner.

**What must now be true elsewhere.** The self-test in `sql/01_schema.sql` no
longer requires all four privileges on every table: `audit_log` is the one
exception, checked separately. RN-30 in `docs/business-rules.md` says the rule
is enforced in the schema too.

## Compliance

The privilege self-test in `sql/01_schema.sql` (run as `retail_app`, see
`deploy/postgresql/README.md`) fails unless `audit_log` has `SELECT` and
`INSERT` only, and it attempts an `UPDATE` and a `DELETE` and expects
`insufficient_privilege`. `tests/test_audit_log_append_only.py` checks that the
schema carries the `REVOKE` and the matching self-test.

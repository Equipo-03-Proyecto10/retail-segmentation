# ADR-0014 — Services own transactions and translate typed write failures

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #163, #164
**Extends:** ADR-0003

## Context

Catalog data-access functions committed individually, while user routes and CLI
commands committed their own work. Administrator installation and transfer need
several statements to succeed together. Catalog functions also returned either
messages or booleans, while user services raised exceptions.

## Decision

Every public write service defines a complete transaction through
`web.db.transactions.atomic`. The outermost service commits once after success
and rolls back on any exception, including a failed commit. Nested services on
the same connection join that transaction; they never commit independently.
Reads made on the request connection before the service are included in the
transaction. A failed nested service must propagate its exception to the owner.

Data-access functions issue parameterized SQL and return data or `None`. They
never commit, roll back, or choose presentation messages. Native psycopg
exceptions cross this boundary. Services translate expected constraint failures
into typed exceptions carrying field messages; unexpected failures propagate to
the shared error handler. Routes and CLI commands only call services and format
their results. No entry point carries SQL or controls a transaction.

Administrator installation, role transfer, and optional demonstration-account
deactivation form one service transaction. The partial unique index and both
application administrator checks remain required.

## Alternatives

- Database functions commit: convenient for single statements, but nested user
  creation would commit before administrator transfer or account deactivation.
- Routes commit: duplicates policy in HTTP and CLI and makes partial writes easy.
- Return strings and booleans: callers must guess which convention an operation
  uses, and field messages end up inside the SQL package.

## Consequences and verification

Services use a small shared transaction wrapper; `web.db` is still the only SQL
package. Regressions check a single outer commit, rollback after partial work,
commit failure, constraint translation, and unchanged administrator enforcement.
Explicit entity forms remain: extracting an all-purpose CRUD framework would
hide their different uploads, parent categories, and role rules. Shared concerns
are validation, transaction handling, failure translation, and pagination.

This proposal is submitted for acceptance with the implementing PR. It extends
the existing layer decision without rewriting the accepted decision's text.

# Database contracts

Every database object published across a service boundary has a Markdown
contract named `contracts/db/<schema>.<object>.v1.md`. The producer owns the
contract. It records:

- the fully qualified object name and whether it is a view or function;
- its owning boundary;
- for a relation, each column's PostgreSQL type and meaning, or for a function,
  its complete signature, result, and error contract;
- every consuming service; and
- what constitutes a breaking change.

Consumers name the relation columns they read, so adding a column is not
breaking. Removing or changing a consumed column, or changing a function's
signature or promised behavior, is breaking. A breaking revision ships an
`_v2` database object and a `.v2.md` contract beside `_v1` and `.v1.md`; v1 is
removed only after it has no consumers.

`contracts/db/grants.csv` is the exact grant manifest. Its header is:

```text
role,object,privilege
```

An object is `schema.relation`, `schema.relation(column)`,
`schema.function(argtypes)` exactly as `regprocedure` prints it, or a bare
schema name for `USAGE`. The manifest includes service and definer roles and is
compared with PostgreSQL in both directions.

Transaction-scoped advisory lock keys are registered in
[`advisory-locks.md`](advisory-locks.md) before reuse.

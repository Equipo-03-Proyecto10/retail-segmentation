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

## Checking the database against the contracts

Both checks run as the database owner after the three scripts, from the
repository root, and print nothing when the database holds. CI runs them in
the `database` job, and any output fails it.

```bash
contracts/db/check_grants.sh            # grants.csv both ways, and ADR-0034's role rules
psql -X -d retail -f contracts/db/check_boundaries.sql   # ADR-0044's four boundary checks
```

`check_grants.sh` prints a `<` line for a manifest row the database lacks, a
`>` line for a grant the manifest lacks, and `assertion N:` for anything else.
It runs inside a transaction it rolls back. `--prelude FILE` runs extra SQL
first, `--grants FILE` swaps the manifest, and `--only N` keeps one assertion,
which is how the tests exercise it.

A function object contains commas, so its row quotes the object the way
`psql --csv` prints it:

```text
svc_sales,"api_stock.consume_v1(smallint,integer,integer)",EXECUTE
```

Each boundary story adds its own rows, its definer's included, in the same
pull request as the grants in `sql/01_schema.sql`.

## Outbox and capture

A boundary that publishes a Redis Stream copies the outbox template, the
`OUTBOX TEMPLATE` block in `sql/01_schema.sql`, into its own section. That
block is the only copy, and `tests/test_outbox_template.py` renders it from
there. It gives the boundary:

- `<suffix>_events.outbox`, keyed by `event_id`, with exactly the six columns
  `event_id`, `stream`, `event_type`, `entity_id`, `occurred_at` and
  `relayed_at`, and a partial index over the rows not yet relayed;
- an AFTER row capture function in `<suffix>_events`. It is SECURITY DEFINER
  and owned by `definer_<suffix>` (`definer_stock` for stock), with
  `search_path` pinned and EXECUTE revoked from PUBLIC. It writes one outbox
  row per change, whichever role made it, the web included;
- schema USAGE for `svc_<suffix>` and `definer_<suffix>` only, INSERT for the
  definer, and SELECT, UPDATE of `relayed_at` and DELETE for the service, whose
  relay publishes and purges;
- a schema-qualified line in `sql/seed-exempt.txt`.

The capture function also calls `pg_notify('<suffix>_outbox', '')`. The
payload is always empty, and only the owner's relay LISTENs on its channel. It
is a wake-up, never a way to carry data or to reach another boundary
(ADR-0044):

| Channel | Owner |
|---|---|
| `users_outbox` | users |
| `products_outbox` | products |
| `categories_outbox` | categories |
| `stores_outbox` | stores |
| `stock_outbox` | stock |
| `preferences_outbox` | customer-preferences |
| `sales_outbox` | sales |
| `segments_outbox` | segments |
| `models_outbox` | model-registry |

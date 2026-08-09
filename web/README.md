# Web system

Flask + Jinja2 web system for Milestone 1. The application itself is S0-03 and
does not exist yet. What is here now is the database migration tooling, so that
every team member builds the same schema from the same source.

## Running the migrations

```bash
# 1. a PostgreSQL 16 to migrate against (S0-02 replaces this with docker compose)
docker run --rm -d --name retail-pg -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 postgres:16
docker exec retail-pg psql -U postgres -c 'CREATE DATABASE retail;'

# 2. configuration
cp .env.example .env          # from the repository root

# 3. apply
cd web
pip install -r requirements.txt
alembic upgrade head
```

`alembic downgrade base` returns the database to empty.

## What the baseline revision is, and why it looks like that

`migrations/versions/0001_frozen_m1_schema.py` carries the frozen M1 schema as
embedded SQL rather than as a few hundred `op.create_table()` calls.

That is a deliberate choice, and the reason is fidelity. The authoritative
physical model is `infra/sql/schema/001_m1_initial_schema.sql`, and it has been
verified against 16 acceptance checks. Several of the objects in it cannot be
expressed in Alembic's core operations at all — the `EXCLUDE USING gist`
constraint that makes segment history correct, four PL/pgSQL functions, nine
triggers, three views, partial indexes with predicates, GIN indexes with
operator classes. Rewriting the verified file into a form that needs
`op.execute()` for the important half of it would add a transcription step and a
place for the two to silently diverge, in exchange for nothing.

So the SQL is embedded **verbatim**, with one edit: the file's outer `BEGIN;`
and `COMMIT;` are removed, because Alembic already runs each revision in a
transaction.

The revision is self-contained rather than reading the `.sql` file at run time.
An applied migration must be immutable — one that reads a file which can later
change is one whose history is not reproducible.

`downgrade()` is real, not a `pass`. It drops the views, the 27 tables with
`CASCADE`, the four functions, and finally the two extensions.

## Proving the migration and the reference DDL agree

The DDL's own header states the contract:

> The first substantive Alembic revision must produce a schema byte-identical to
> this file.

The checksum in the revision docstring records provenance only. Equivalence is
proven by building both and diffing them:

```bash
# reference database, straight from the DDL
docker exec retail-pg psql -U postgres -c 'CREATE DATABASE ref_db;'
docker exec retail-pg psql -U postgres -d ref_db -v ON_ERROR_STOP=1 \
  -f /repo/infra/sql/schema/001_m1_initial_schema.sql

# application database, from the migration
DATABASE_MIGRATION_URL=postgresql+psycopg://postgres:postgres@localhost:5432/app_db \
  alembic upgrade head

# they must be identical apart from alembic_version
docker exec retail-pg pg_dump -U postgres --schema-only ref_db > /tmp/ref.sql
docker exec retail-pg pg_dump -U postgres --schema-only app_db > /tmp/app.sql
diff /tmp/ref.sql /tmp/app.sql
```

Then run the acceptance checks against the migrated database:

```bash
docker exec retail-pg psql -U postgres -d app_db -f /repo/infra/sql/schema/verify_m1_schema.sql
```

All 16 checks must behave as their `expected:` lines state. Checks 2, 3, 9, 10,
11, 12, 13, 14 and 15 pass by **raising an error** — an `ERROR` line there is the
success condition and a silent success is the failure.

This is the check the Definition of Done requires in CI. There is no CI yet.

## For whoever picks up S0-03

This wiring is intentionally minimal so the application factory is built around
it rather than replacing it:

- `migrations/env.py` already carries the naming convention from §2 of
  `docs/data/postgresql-model.md`. Do not drop it — without it, autogenerate
  emits anonymous constraints that a later migration cannot drop by name, and it
  will propose renaming every constraint already in the frozen schema.
- `target_metadata` is an empty `MetaData` carrying that convention. Point it at
  the declarative base's metadata once models exist.
- `env.py` reads `DATABASE_MIGRATION_URL`, not `DATABASE_URL`. The application
  must connect as the restricted role (D-14). Keeping these separate is what
  makes the append-only audit trail real rather than decorative.

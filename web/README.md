# Web application

The Flask monolith. The skeleton and its layers landed with F3-01; the modules
that fill them are the remaining Phase 3 stories in
[`../docs/backlog.md`](../docs/backlog.md).

## Layout

Organized by layers, which is a graded requirement:

```
web/
  app.py          application factory and entry point
  config/         configuration read from environment variables
  middleware/     cross-cutting request handling: authorization
  routes/         blueprints, one per module
  services/       business logic, with no knowledge of HTTP
  db/             connection handling and parameterized queries
  templates/      Jinja2
  static/         CSS, JS, images
```

A layer calls the one below it and never the reverse. A route reads the request,
calls a service and renders a template; a service holds the logic and never
touches a request; every SQL statement in the application lives in `db/`. A
middleware sits in front of all of them: it decides whether a request reaches a
route at all, and holds no business logic and no SQL of its own.

### The same structure in MVC vocabulary

| MVC | Here |
|---|---|
| Controller | `routes/` — reads the request, calls a service, renders a template |
| View | `templates/` — a pure function of the values the route passes it |
| Model | `services/` for the business rules, `db/` for data access |

What MVC calls the model is deliberately split in two. Why, and why the
directories are not named `controllers/`, `models/` and `views/`:
[ADR-0003](../docs/adr/0003-layered-architecture-with-an-explicit-service-layer.md).

One directory from the intended layout is still absent because nothing has a
file to put in it yet: `uploads/` (gitignored), which only the local file store
and the images of the first delivery use.

Uploaded files (a product's picture) are kept by a file store, not by the
database ([ADR-0091](../docs/adr/0091-the-web-keeps-its-files-in-a-private-gcs-bucket-behind-a-file-store-port-and-only-their-metadata-in-postgresql.md)).
`services/files.py` holds the `FileStore` port and its two backends: a private
Cloud Storage bucket in production, and a directory under `UPLOAD_DIR` on a
developer's machine and in tests (`FILE_STORE_BACKEND`, default `local`). The
`stored_file` table keeps only each file's metadata. An upload writes the bytes,
then the row, in the product's transaction; a store that is down leaves no row,
and a refused row leaves no object. Only `services/files.py` may import the
Google client, and only when the `gcs` backend is first used.

Writes go through services, which own a complete transaction. Data-access
functions return data or raise psycopg exceptions; services translate expected
refusals into typed field errors. `db/transactions.py` is the only commit and
rollback implementation, including nested administrator operations. See
[ADR-0014](../docs/adr/0014-service-owned-transactions-and-typed-write-failures.md).
Pagination arithmetic lives in `services/pagination.py` so the audit service
never needs to import the HTTP layer. Entity forms remain explicit because
products, category parents and users have different validation and file rules.

New user passwords require at least 12 characters, using the same minimum as
the instance CLI. Existing stored credentials continue to verify normally.
Application events follow the [logging convention](../docs/logging.md).

## Running it

From the repository root, with `web/requirements.txt` installed:

```bash
flask --app web.app run      # http://localhost:5000
python -m web.app            # the same application, on $PORT
```

The tests are in [`../tests/`](../tests/) and run with `pytest` from the
repository root.

## Rules that apply here

- Every SQL statement is parameterized. No string interpolation, anywhere.
- Schema changes belong in `../sql/01_schema.sql`, never in application code.
- Every route declares what reaching it requires — `@public` or
  `@requires(...)` from `web.middleware`. A route that declares nothing is
  refused, and `tests/test_authz.py` fails the build rather than letting it
  ship. The permission matrix is `docs/requirements.md` §3, transcribed in
  `middleware/authz.py`.
- The single-administrator rule is enforced here *and* by a partial unique index
  in the schema. Both halves, or neither counts.
- Configuration comes from environment variables. No secrets in source.

Full rules in [`../AGENTS.md`](../AGENTS.md).

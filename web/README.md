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
  routes/         blueprints, one per module
  services/       business logic, with no knowledge of HTTP
  db/             connection handling and parameterized queries
  templates/      Jinja2
  static/         CSS, JS, images
```

A layer calls the one below it and never the reverse. A route reads the request,
calls a service and renders a template; a service holds the logic and never
touches a request; every SQL statement in the application lives in `db/`.

Two directories from the intended layout are absent because nothing has a file
to put in them yet: `middleware/` arrives with the authorization middleware
(F4-01), and `uploads/` with image handling (F3-07, gitignored).

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
- The single-administrator rule is enforced here *and* by a partial unique index
  in the schema. Both halves, or neither counts.
- Configuration comes from environment variables. No secrets in source.

Full rules in [`../AGENTS.md`](../AGENTS.md).

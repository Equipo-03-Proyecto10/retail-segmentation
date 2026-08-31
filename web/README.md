# Web application

The Flask monolith. Built by the Phase 3 stories in
[`../docs/backlog.md`](../docs/backlog.md); this directory currently holds only
its dependency files.

## Intended layout

Organized by layers, which is a graded requirement:

```
web/
  app.py          application factory and entry point
  config/         configuration read from environment variables
  routes/         blueprints, one per module
  services/       business logic
  db/             connection handling and parameterized queries
  middleware/     authentication and authorization
  templates/      Jinja2
  static/         CSS, JS, images
  uploads/        uploaded images (gitignored)
```

## Rules that apply here

- Every SQL statement is parameterized. No string interpolation, anywhere.
- Schema changes belong in `../sql/01_schema.sql`, never in application code.
- The single-administrator rule is enforced here *and* by a partial unique index
  in the schema. Both halves, or neither counts.
- Configuration comes from environment variables. No secrets in source.

Full rules in [`../AGENTS.md`](../AGENTS.md).

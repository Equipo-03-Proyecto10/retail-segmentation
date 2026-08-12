# Infrastructure: containers, GCP

## Local stack (S0-02)

```bash
cp .env.example .env      # from the repository root
make up                   # postgres, mongo, redis, web — waits for all four to be healthy
make migrate              # alembic upgrade head, then locks down app_runtime
make down                 # stop the stack, keep the volumes
make reset                # stop the stack AND drop the volumes, then start clean
make logs                 # follow all four containers
```

`web` is a placeholder container until S0-03 builds the Flask application —
see `web/Dockerfile`.

## Two Postgres roles (D-14, R-18)

`audit_log` is append-only, enforced by a trigger *and* by grants. The grant
only matters if the application never connects as the role that owns the
schema — otherwise a `REVOKE` against that role is a no-op.

- `infra/sql/init/01-app-runtime-role.sh` runs once, the first time the
  `postgres` named volume initializes. It creates `app_runtime` (password from
  `POSTGRES_RUNTIME_PASSWORD`) and grants it `SELECT`/`INSERT`/`UPDATE`/`DELETE`
  on the schema.
- `audit_log` does not exist yet at that point, so this can't be a second file
  in `infra/sql/init/` — the Postgres entrypoint auto-runs everything there
  against a database with no tables. `infra/sql/post-init/audit-log-revoke.sql`
  holds the `REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM app_runtime`
  that finishes closing the gap; `make migrate` pipes it into `psql` right
  after `alembic upgrade head`.
- `.env`'s `DATABASE_MIGRATION_URL` connects as the schema owner (`postgres`).
  `DATABASE_URL` connects as `app_runtime`. The application must always use the
  latter — see `docs/data/postgresql-model.md` D-14.

Both init scripts only re-run against a fresh volume, i.e. after `make reset`.

## GCP

Not yet built. Kubernetes is deferred pending an independent scaling need
(`docs/adr/0004-defer-gke.md`); M1 targets Compute Engine and this
`docker-compose.yml` locally.

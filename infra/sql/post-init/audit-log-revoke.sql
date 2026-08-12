-- Second half of D-14 / R-18. audit_log does not exist when Postgres first
-- initializes its volume, so this cannot live in infra/sql/init/ — anything
-- there is auto-executed by the Postgres entrypoint against a database that
-- has no tables yet. It has to run after the frozen schema is migrated in.
-- `make migrate` pipes this file into psql (schema owner) right after
-- `alembic upgrade head`.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM app_runtime;

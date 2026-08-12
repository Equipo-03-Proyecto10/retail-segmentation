#!/bin/sh
# Runs once, the first time the postgres named volume is initialized (Postgres
# entrypoint convention for docker-entrypoint-initdb.d). Creates the
# restricted runtime role that D-14 / R-18 require: audit_log is append-only
# by trigger *and* by grant, and the grant only means something if the
# application never connects as the schema owner.
#
# This is the same statements as the commented-out block at the bottom of
# infra/sql/schema/001_m1_initial_schema.sql (§9), executed here instead of in
# Alembic because role creation is a one-time, per-environment operation, not
# a schema migration.
set -eu

: "${POSTGRES_RUNTIME_PASSWORD:?POSTGRES_RUNTIME_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v runtime_password="$POSTGRES_RUNTIME_PASSWORD" <<-'SQL'
	CREATE ROLE app_runtime LOGIN PASSWORD :'runtime_password';
	GRANT USAGE ON SCHEMA public TO app_runtime;
	GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO app_runtime;
	GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA public TO app_runtime;
	ALTER DEFAULT PRIVILEGES IN SCHEMA public
	    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_runtime;
SQL

# audit_log does not exist yet at first init (the frozen schema is applied
# later by Alembic, using DATABASE_MIGRATION_URL). The REVOKE therefore can't
# run here — there's nothing to revoke it from. It has to run after the first
# `make migrate`. See infra/README.md.

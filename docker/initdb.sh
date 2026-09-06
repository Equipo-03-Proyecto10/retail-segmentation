#!/bin/sh
# Runs the three ordered scripts against a fresh PostgreSQL container.
#
# The postgres image runs everything in /docker-entrypoint-initdb.d/ once, on
# an empty data directory. It cannot simply be handed the three .sql files:
# each would run in its own psql session against the default database, so the
# `\c retail` in 00_create_database.sql would not carry over and 01 and 02
# would build the schema in the wrong database.
#
# So the order lives here instead, and it is exactly the order in the README,
# in .github/workflows/ci.yml and in the Definition of Done. The scripts
# themselves are mounted read-only and are not modified.
set -eu

SQL=/opt/sql

echo "initdb: 00_create_database.sql"
psql -v ON_ERROR_STOP=1 \
     -v app_password="${APP_DB_PASSWORD:-retail_app}" \
     -U "$POSTGRES_USER" -f "$SQL/00_create_database.sql"

echo "initdb: 01_schema.sql"
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d retail -f "$SQL/01_schema.sql"

echo "initdb: 02_seed_30_per_table.sql"
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d retail -f "$SQL/02_seed_30_per_table.sql"

echo "initdb: done — retail is created, schemad and seeded."

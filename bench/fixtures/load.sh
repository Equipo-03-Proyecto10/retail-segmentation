#!/bin/sh
# Runs after docker/initdb.sh in the bench's PostgreSQL: the 10,000 products,
# into the database the three scripts built.
set -eu
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d retail -f /opt/bench/products-10k.sql

-- =========================================================
-- 00_create_database.sql
-- MOSAIQ — database, extensions and the application role.
--
-- First of the three scripts. They run in order against an empty PostgreSQL:
--
--   psql -v ON_ERROR_STOP=1 -f sql/00_create_database.sql
--   psql -v ON_ERROR_STOP=1 -d retail -f sql/01_schema.sql
--   psql -v ON_ERROR_STOP=1 -d retail -f sql/02_seed_30_per_table.sql
--
-- No password is written here. The application role's password arrives as a
-- psql variable, which on the instance is read from the environment:
--
--   psql -v app_password="$APP_DB_PASSWORD" -f sql/00_create_database.sql
--
-- Without that variable the script falls back to the local development
-- default already published in .env.example, so CI and a fresh clone both run
-- it unattended. That default is not a secret and never reaches the instance.
-- =========================================================

\if :{?app_password}
\else
\set app_password 'retail_app'
\endif

CREATE DATABASE retail
  WITH ENCODING 'UTF8'
  TEMPLATE template0;

\c retail

-- pg_trgm backs the customer and product name searches F3-05 performs.
--
-- pgcrypto is deliberately not installed. gen_random_uuid() has been core
-- since PostgreSQL 13, and passwords are hashed by the application with
-- argon2 (web/requirements.txt), never by the database — so the one thing
-- pgcrypto would have been for does not happen here.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- The application connects through a restricted role, never as the role that
-- owns the schema — docs/backlog.md F1-05. psql does not substitute variables
-- inside a dollar-quoted body, so the password reaches the block through a
-- session setting rather than being interpolated into it.
SELECT set_config('mosaiq.app_password', :'app_password', false);

DO $$
DECLARE
  pw text := current_setting('mosaiq.app_password');
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'retail_app') THEN
    EXECUTE format('CREATE ROLE retail_app LOGIN PASSWORD %L', pw);
  ELSE
    EXECUTE format('ALTER ROLE retail_app PASSWORD %L', pw);
  END IF;
END
$$;

GRANT CONNECT ON DATABASE retail TO retail_app;
GRANT USAGE ON SCHEMA public TO retail_app;

-- These apply to the tables 01_schema.sql is about to create, which is why
-- they are granted here rather than after the schema exists. The application
-- reads and writes rows; it never creates or alters a table.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO retail_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO retail_app;

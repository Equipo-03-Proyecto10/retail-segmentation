-- =========================================================
-- 00_create_database.sql
-- MOSAIQ — database, extensions, the application role, and the service and
-- definer roles of the distributed stage (ADR-0034, ADR-0044).
--
-- Re-runnable: a second run on the same cluster creates nothing twice and only
-- re-applies role attributes and passwords. Roles belong to the cluster, so
-- the services' roles are created here, beside retail_app, not in 01.
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
--
-- Each service role reads its password the same way, from
-- `-v svc_<suffix>_password=...` (SVC_<SUFFIX>_DB_PASSWORD in .env.example),
-- and falls back to its own role name locally.
-- =========================================================

\if :{?app_password}
\else
\set app_password 'retail_app'
\endif

-- Only when absent, so a second run on the same cluster succeeds.
SELECT 'CREATE DATABASE retail WITH ENCODING ''UTF8'' TEMPLATE template0'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'retail')\gexec

\c retail

-- pg_trgm backs the customer and product name searches F3-05 performs.
--
-- pgcrypto is deliberately not installed. gen_random_uuid() has been core
-- since PostgreSQL 13, and passwords are hashed by the application with
-- argon2 (web/requirements.txt), never by the database — so the one thing
-- pgcrypto would have been for does not happen here.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- btree_gist lets an exclusion constraint compare a uuid for equality next to a
-- range overlap: customer_segment_history uses it so one customer's segment
-- intervals can never overlap (#350, ADR-0029). A trusted extension since
-- PostgreSQL 13, so the database owner may create it.
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- The application connects through a restricted role, never as the role that
-- owns the schema — docs/backlog.md F1-05. psql does not substitute variables
-- inside a dollar-quoted body, so the password reaches the block through a
-- session setting rather than being interpolated into it. SET, not
-- SELECT set_config(): a SELECT returns the password as a result row, and psql
-- prints it to whoever runs the script. RESET below drops it from the session.
SET mosaiq.app_password = :'app_password';

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

RESET mosaiq.app_password;

GRANT CONNECT ON DATABASE retail TO retail_app;
GRANT USAGE ON SCHEMA public TO retail_app;

-- These apply to the tables 01_schema.sql is about to create, which is why
-- they are granted here rather than after the schema exists. The application
-- reads and writes rows; it never creates or alters a table.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO retail_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO retail_app;

-- =========================================================
-- Service and definer roles (ADR-0034, ADR-0044, F13-07)
--
-- One LOGIN role per service, plus the bench-only products build
-- (ADR-0042). Each holds CONNECT and nothing else here; 01_schema.sql grants
-- it its own tables, and contracts/db/grants.csv lists every grant.
--
-- A password arrives only when given (`-v svc_<suffix>_password=...`), as a
-- session setting the DO block reads, for the reason retail_app's does. A role
-- given none falls back to its own name: a local default, never one for the
-- instance.
-- =========================================================

\if :{?svc_auth_password}
SET mosaiq.svc_auth_password = :'svc_auth_password';
\endif
\if :{?svc_users_password}
SET mosaiq.svc_users_password = :'svc_users_password';
\endif
\if :{?svc_products_password}
SET mosaiq.svc_products_password = :'svc_products_password';
\endif
\if :{?svc_categories_password}
SET mosaiq.svc_categories_password = :'svc_categories_password';
\endif
\if :{?svc_stores_password}
SET mosaiq.svc_stores_password = :'svc_stores_password';
\endif
\if :{?svc_stock_password}
SET mosaiq.svc_stock_password = :'svc_stock_password';
\endif
\if :{?svc_customers_password}
SET mosaiq.svc_customers_password = :'svc_customers_password';
\endif
\if :{?svc_preferences_password}
SET mosaiq.svc_preferences_password = :'svc_preferences_password';
\endif
\if :{?svc_sales_password}
SET mosaiq.svc_sales_password = :'svc_sales_password';
\endif
\if :{?svc_segments_password}
SET mosaiq.svc_segments_password = :'svc_segments_password';
\endif
\if :{?svc_recommendations_password}
SET mosaiq.svc_recommendations_password = :'svc_recommendations_password';
\endif
\if :{?svc_engagement_password}
SET mosaiq.svc_engagement_password = :'svc_engagement_password';
\endif
\if :{?svc_checkins_password}
SET mosaiq.svc_checkins_password = :'svc_checkins_password';
\endif
\if :{?svc_campaigns_password}
SET mosaiq.svc_campaigns_password = :'svc_campaigns_password';
\endif
\if :{?svc_audit_trail_password}
SET mosaiq.svc_audit_trail_password = :'svc_audit_trail_password';
\endif
\if :{?svc_products_bench_password}
SET mosaiq.svc_products_bench_password = :'svc_products_bench_password';
\endif

DO $$
DECLARE
  r  text;
  pw text;
BEGIN
  FOREACH r IN ARRAY ARRAY[
    'svc_auth',
    'svc_users',
    'svc_products',
    'svc_categories',
    'svc_stores',
    'svc_stock',
    'svc_customers',
    'svc_preferences',
    'svc_sales',
    'svc_segments',
    'svc_recommendations',
    'svc_engagement',
    'svc_checkins',
    'svc_campaigns',
    'svc_audit_trail',
    'svc_products_bench'
  ] LOOP
    pw := coalesce(nullif(current_setting('mosaiq.' || r || '_password', true), ''), r);
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I LOGIN', r);
    END IF;
    EXECUTE format(
      'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
      r, pw);
    EXECUTE format('GRANT CONNECT ON DATABASE retail TO %I', r);
    PERFORM set_config('mosaiq.' || r || '_password', '', false);
  END LOOP;
END
$$;

-- Owners of SECURITY DEFINER functions, never able to sign in. Each receives
-- its privileges in its own boundary's section of 01_schema.sql, so a
-- function runs with exactly what that one boundary needs.
DO $$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY[
    'definer_stock',
    'definer_audit',
    'definer_guard',
    'definer_users',
    'definer_products',
    'definer_categories',
    'definer_stores',
    'definer_preferences',
    'definer_sales',
    'definer_segments'
  ] LOOP
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN', r);
    END IF;
    EXECUTE format(
      'ALTER ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS', r);
  END LOOP;
END
$$;

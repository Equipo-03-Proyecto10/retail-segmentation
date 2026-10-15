-- ADR-0044's boundary checks. Run as the database owner, from the repository
-- root, after the three scripts; it prints nothing when every boundary holds:
--
--   psql -X -v ON_ERROR_STOP=1 -d retail -f contracts/db/check_boundaries.sql
--
-- The SQL is ADR-0044's compliance text at commit 69dac6b, with one change:
-- check 2's expected set is read from contracts/db/grants.csv instead of
-- seventeen hard-coded pairs, as the draft's own comment says, so each
-- boundary story only adds its rows there. The definer rule (the draft's
-- check 3) lives in contracts/db/check_grants.sh, assertion 6.
\set ON_ERROR_STOP 1
\set QUIET on
\pset tuples_only on
\pset format unaligned

BEGIN;

CREATE TEMP TABLE grant_row (role text, object text, privilege text) ON COMMIT DROP;
\copy grant_row FROM 'contracts/db/grants.csv' WITH (FORMAT csv, HEADER true)

-- 1. Outside api_* schemas, no relation is reachable by two boundaries.
--    (Which role it is belongs to contracts/db/grants.csv, ADR-0034.)
SELECT 'check 1: ' || n.nspname || '.' || c.relname || ' is reachable by '
       || array_agg(DISTINCT regexp_replace(r.rolname, '_bench$', ''))::text
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  JOIN pg_roles r ON r.rolname LIKE 'svc\_%'
 WHERE c.relkind IN ('r', 'p', 'v', 'm')
   AND n.nspname NOT LIKE 'api\_%'
   AND n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND (has_table_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
        OR has_any_column_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES'))
 GROUP BY n.nspname, c.relname
HAVING count(DISTINCT regexp_replace(r.rolname, '_bench$', '')) > 1;

-- 2. Cross-boundary access is exactly the published list. Expected: the
--    svc_* rows of grants.csv with SELECT or EXECUTE on an api_* object, less
--    each producer's SELECT on its own views, with a function's argument list
--    dropped to match `actual`.
WITH expected(role, obj) AS (
    SELECT role, regexp_replace(object, '\(.*\)$', '')
      FROM grant_row
     WHERE role LIKE 'svc\_%'
       AND privilege IN ('SELECT', 'EXECUTE')
       AND object LIKE 'api\_%'
       AND NOT (privilege = 'SELECT'
                AND role = 'svc_' || substr(split_part(object, '.', 1), 5))
), actual(role, obj) AS (
    SELECT r.rolname, n.nspname || '.' || c.relname
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_roles r ON r.rolname LIKE 'svc\_%'
     WHERE n.nspname LIKE 'api\_%'
       AND has_table_privilege(r.oid, c.oid, 'SELECT')
       AND r.rolname <> 'svc_' || substr(n.nspname, 5)
    UNION
    SELECT r.rolname, n.nspname || '.' || p.proname
      FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
      JOIN pg_roles r ON r.rolname LIKE 'svc\_%'
     WHERE n.nspname LIKE 'api\_%'
       AND has_function_privilege(r.oid, p.oid, 'EXECUTE')
)
SELECT 'check 2: ' || problem || ' ' || role || ' -> ' || obj FROM (
    (SELECT 'unexpected' AS problem, role, obj FROM actual
     EXCEPT SELECT 'unexpected', role, obj FROM expected)
    UNION ALL
    (SELECT 'missing', role, obj FROM expected
     EXCEPT SELECT 'missing', role, obj FROM actual)
) diff;

-- 3. Every outbox has exactly the six key-only columns.
SELECT 'check 3: ' || table_schema || '.outbox has ' || columns FROM (
    SELECT table_schema,
           string_agg(column_name, ',' ORDER BY ordinal_position) AS columns
      FROM information_schema.columns
     WHERE table_schema LIKE '%\_events' AND table_name = 'outbox'
     GROUP BY table_schema
) outbox
 WHERE columns <> 'event_id,stream,event_type,entity_id,occurred_at,relayed_at';

-- 4. A capture function only names streams its own boundary produces.
SELECT 'check 4: ' || n.nspname || ' names ' || m[1]
  FROM pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace,
       regexp_matches(p.prosrc, '''(stream:[a-z_]+\.v[0-9]+\.[a-z_]+)''', 'g') AS m
 WHERE n.nspname LIKE '%\_events'
   AND split_part(substr(m[1], 8), '.', 1) <> regexp_replace(n.nspname, '_events$', '');

ROLLBACK;

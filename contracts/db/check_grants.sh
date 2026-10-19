#!/usr/bin/env bash
# The database's grants against contracts/db/grants.csv, and ADR-0034's other
# role assertions. Run as the database owner after the three scripts; it
# prints nothing when the database matches. The SQL is ADR-0034's compliance
# text at commit 69dac6b, with one change: the grant matrix compares the
# definer_* roles too, so definer_audit's row is checked like a service's.
#
#   contracts/db/check_grants.sh [--grants FILE] [--prelude FILE] [--only N]
#
# The connection comes from PG* variables; PGDATABASE may be a URI and
# defaults to retail. Everything runs in one transaction that is rolled back,
# so --prelude can change grants for a test without leaving a trace.
#
# A diff line from assertion 1 starts with "<" (in grants.csv, missing from
# the database) or ">" (in the database, missing from grants.csv). Every other
# problem line starts with "assertion N:".
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
grants=$here/grants.csv
prelude=
only=
while (($#)); do
  case $1 in
    --grants) grants=$2; shift 2 ;;
    --prelude) prelude=$2; shift 2 ;;
    --only) only=$2; shift 2 ;;
    *) printf 'usage: %s [--grants FILE] [--prelude FILE] [--only N]\n' "$0" >&2; exit 2 ;;
  esac
done

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
sql=$work/check.sql
wanted() { [[ -z $only || $only == "$1" ]]; }

{
  printf '%s\n' '\set ON_ERROR_STOP 1' '\set QUIET on' '\pset tuples_only on' 'BEGIN;'
  [[ -n $prelude ]] && printf '\\i %s\n' "$prelude"
  printf '%s\n' '\pset format unaligned'
} > "$sql"

if wanted 1; then
  cat >> "$sql" <<SQL
\pset format csv
\o $work/actual.csv
WITH svc AS (
  SELECT oid, rolname FROM pg_roles WHERE rolname LIKE 'svc\_%' OR rolname LIKE 'definer\_%'
), rel AS (
  SELECT c.oid, n.nspname || '.' || c.relname AS object
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind IN ('r', 'p', 'v', 'm')
    AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    AND n.nspname NOT LIKE 'pg\_%'
)
SELECT s.rolname, r.object, p.priv
FROM svc s CROSS JOIN rel r
CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE',
                        'TRUNCATE','REFERENCES','TRIGGER']) AS p(priv)
WHERE has_table_privilege(s.oid, r.oid, p.priv)
UNION ALL
SELECT s.rolname, r.object || '(' || a.attname || ')', p.priv
FROM svc s CROSS JOIN rel r
JOIN pg_attribute a ON a.attrelid = r.oid AND a.attnum > 0 AND NOT a.attisdropped
CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','REFERENCES']) AS p(priv)
WHERE has_column_privilege(s.oid, r.oid, a.attnum, p.priv)
  AND NOT has_table_privilege(s.oid, r.oid, p.priv)
UNION ALL
SELECT s.rolname, f.oid::regprocedure::text, 'EXECUTE'
FROM svc s CROSS JOIN pg_proc f JOIN pg_namespace n ON n.oid = f.pronamespace
WHERE n.nspname LIKE 'api\_%' AND has_function_privilege(s.oid, f.oid, 'EXECUTE')
UNION ALL
SELECT s.rolname, n.nspname, 'USAGE'
FROM svc s CROSS JOIN pg_namespace n
WHERE n.nspname NOT IN ('public', 'pg_catalog', 'information_schema')
  AND n.nspname NOT LIKE 'pg\_%'
  AND has_schema_privilege(s.oid, n.oid, 'USAGE');
\o
\pset format unaligned
SQL
fi

if wanted 2; then
  cat >> "$sql" <<'SQL'
-- Exactly the fifteen service LOGIN roles, plus the bench role.
SELECT 'assertion 2: missing or extra LOGIN role ' || coalesce(x.name, r.rolname)
FROM unnest(ARRAY['svc_auth','svc_users','svc_products','svc_categories',
  'svc_stores','svc_stock','svc_customers','svc_preferences','svc_sales','svc_segments',
  'svc_recommendations','svc_engagement','svc_checkins','svc_campaigns','svc_audit_trail',
  'svc_products_bench']) AS x(name)
FULL JOIN (SELECT rolname FROM pg_roles WHERE rolname LIKE 'svc\_%' AND rolcanlogin) r
  ON r.rolname = x.name
WHERE x.name IS NULL OR r.rolname IS NULL;
SQL
fi

if wanted 3; then
  cat >> "$sql" <<'SQL'
-- No service privilege on a web-only table.
SELECT 'assertion 3: ' || r.rolname || ' reaches web-only ' || t FROM pg_roles r
CROSS JOIN unnest(ARRAY['app_session','sales_load','sales_load_rejection','experiment',
  'experiment_group','experiment_assignment','experiment_exposure',
  'experiment_conversion','notification','notification_recipient']) AS t
WHERE r.rolname LIKE 'svc\_%'
  AND (has_table_privilege(r.oid, ('public.' || t)::regclass,
         'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
       OR has_any_column_privilege(r.oid, ('public.' || t)::regclass,
         'SELECT, INSERT, UPDATE, REFERENCES'));
SQL
fi

if wanted 4; then
  cat >> "$sql" <<'SQL'
-- No service holds DELETE or TRUNCATE on the five masters.
SELECT 'assertion 4: ' || r.rolname || ' may delete from ' || t FROM pg_roles r
CROSS JOIN unnest(ARRAY['public.customer','public.product','public.app_user',
  'public.campaign','public.store']) AS t
WHERE r.rolname LIKE 'svc\_%' AND has_table_privilege(r.oid, t, 'DELETE, TRUNCATE');
SQL
fi

if wanted 5; then
  cat >> "$sql" <<'SQL'
-- Only svc_audit_trail reads the audit log, and no service writes it.
SELECT 'assertion 5: ' || rolname || ' reaches audit_log' FROM pg_roles
WHERE rolname LIKE 'svc\_%'
  AND (has_table_privilege(oid, 'public.audit_log', 'INSERT, UPDATE, DELETE, TRUNCATE')
       OR (rolname <> 'svc_audit_trail' AND has_table_privilege(oid, 'public.audit_log', 'SELECT')));
SQL
fi

if wanted 6; then
  cat >> "$sql" <<'SQL'
-- Every SECURITY DEFINER function is owned by a NOLOGIN, non-superuser
-- definer_* role, pins search_path, and is not executable by PUBLIC.
SELECT 'assertion 6: ' || f.oid::regprocedure || ' owned by ' || o.rolname
FROM pg_proc f
JOIN pg_roles o ON o.oid = f.proowner
JOIN pg_namespace n ON n.oid = f.pronamespace
WHERE f.prosecdef
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND (o.rolname NOT LIKE 'definer\_%' OR o.rolcanlogin OR o.rolsuper
       OR NOT EXISTS (SELECT 1 FROM unnest(f.proconfig) AS c WHERE c LIKE 'search\_path=%')
       OR has_function_privilege('public', f.oid, 'EXECUTE'));
SQL
fi

if wanted 7; then
  cat >> "$sql" <<'SQL'
-- No service role owns a schema, a relation or a function.
SELECT 'assertion 7: ' || kind || ' ' || name || ' is owned by a service role' FROM (
  SELECT 'schema' AS kind, nspname::text AS name FROM pg_namespace
    WHERE pg_get_userbyid(nspowner) LIKE 'svc\_%'
  UNION ALL SELECT 'relation', relname::text FROM pg_class
    WHERE pg_get_userbyid(relowner) LIKE 'svc\_%'
  UNION ALL SELECT 'function', proname::text FROM pg_proc
    WHERE pg_get_userbyid(proowner) LIKE 'svc\_%'
) owned;
SQL
fi

if wanted 8; then
  cat >> "$sql" <<'SQL'
-- At most one service role per base table; svc_products_bench counts as products.
SELECT 'assertion 8: ' || base_table || ' is held by ' || holders::text FROM (
  SELECT n.nspname || '.' || c.relname AS base_table,
         array_agg(DISTINCT replace(r.rolname, 'svc_products_bench', 'svc_products')) AS holders
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  CROSS JOIN pg_roles r
  WHERE c.relkind IN ('r', 'p')
    AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    AND n.nspname NOT LIKE 'pg\_%'
    AND r.rolname LIKE 'svc\_%'
    AND (has_table_privilege(r.oid, c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
         OR has_any_column_privilege(r.oid, c.oid, 'SELECT, INSERT, UPDATE, REFERENCES'))
  GROUP BY 1
  HAVING count(DISTINCT replace(r.rolname, 'svc_products_bench', 'svc_products')) > 1
) shared;
SQL
fi

printf '%s\n' 'ROLLBACK;' >> "$sql"

if ! psql -X -q -At -d "${PGDATABASE:-retail}" -f "$sql" > "$work/out.txt" 2>&1; then
  cat "$work/out.txt"
  exit 1
fi

{
  if wanted 1; then
    diff <(tail -n +2 "$grants" | LC_ALL=C sort) <(LC_ALL=C sort "$work/actual.csv") \
      | grep -E '^[<>]'
  fi
  cat "$work/out.txt"
} > "$work/problems.txt"
cat "$work/problems.txt"
[[ ! -s $work/problems.txt ]]

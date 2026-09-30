#!/usr/bin/env bash
# MOSAIQ — does a database still match sql/01_schema.sql? #346.
#
# deploy.sh never touches the database (schema changes are a person's job,
# AGENTS.md), so nothing tells you when the instance falls behind the code it
# runs. #346 was exactly that: the instance database predated the exposure
# trigger and the append-only revokes, and no step could have noticed.
#
# Read-only on both sides. Two modes:
#
#   check-schema-drift.sh dump <out-dir>
#       Writes schema.sql (pg_dump -s, no owners, no privileges) and
#       privileges.txt (what the application role may do to each table) for the
#       database in the PG* environment. Run it against a clean build of the
#       three scripts at the deployed commit, and against the instance.
#
#   check-schema-drift.sh compare <reference-dir> <live-dir>
#       Diffs the two. Exit 0 when identical, 1 when they differ, with the
#       difference printed.
#
# Typical use, on the instance and on a workstation with a scratch PostgreSQL:
#
#   git checkout <deployed-sha>
#   psql -v ON_ERROR_STOP=1 -f sql/00_create_database.sql
#   psql -v ON_ERROR_STOP=1 -d retail -f sql/01_schema.sql
#   deploy/check-schema-drift.sh dump /tmp/reference
#   ...and on the instance:  sudo -u postgres PGDATABASE=retail \
#       deploy/check-schema-drift.sh dump /tmp/live
#   deploy/check-schema-drift.sh compare /tmp/reference /tmp/live
#
# Only the schema is compared; the seed is data and is expected to differ.

set -euo pipefail

APP_ROLE=${APP_ROLE:-retail_app}
PGDATABASE=${PGDATABASE:-retail}
export PGDATABASE

usage() {
  sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//' >&2
  exit 2
}

dump() {
  local out=$1
  mkdir -p "$out"
  pg_dump --schema-only --no-owner --no-privileges --no-comments \
    | grep -v -E '^(-- Dumped (from|by)|\\(un)?restrict )' >"$out/schema.sql"
  # The role is a psql variable, quoted by psql, never spliced into the text.
  # Start with every privilege present in the catalog, then ask PostgreSQL for
  # the role's effective privilege.  Looking only for rows granted directly to
  # the role would miss a grant inherited through PUBLIC or a role membership.
  psql -X -A -t -v ON_ERROR_STOP=1 -v role="$APP_ROLE" <<'SQL' >"$out/privileges.txt"
WITH candidate AS (
    SELECT DISTINCT table_name, privilege_type
      FROM information_schema.table_privileges
     WHERE table_schema = 'public'
)
SELECT c.table_name || ' ' || c.privilege_type
  FROM candidate AS c
 WHERE has_table_privilege(
           :'role', format('%I.%I', 'public', c.table_name), c.privilege_type
       )
 ORDER BY c.table_name, c.privilege_type;
SQL
  echo "wrote $out/schema.sql and $out/privileges.txt"
}

compare() {
  local reference=$1 live=$2 status=0 file
  for file in schema.sql privileges.txt; do
    if [[ ! -f "$reference/$file" || ! -f "$live/$file" ]]; then
      echo "missing $file in $reference or $live" >&2
      exit 2
    fi
    if ! diff -u --label "reference/$file" --label "live/$file" \
      "$reference/$file" "$live/$file"; then
      status=1
    fi
  done
  if [[ $status -eq 0 ]]; then
    echo "no drift: schema and $APP_ROLE privileges match"
  else
    echo "DRIFT: '-' lines are in the reference and missing from live" >&2
  fi
  return $status
}

case "${1:-}" in
  dump) [[ $# -eq 2 ]] || usage; dump "$2" ;;
  compare) [[ $# -eq 3 ]] || usage; compare "$2" "$3" ;;
  *) usage ;;
esac

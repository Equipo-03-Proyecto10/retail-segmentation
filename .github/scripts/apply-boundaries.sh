#!/usr/bin/env bash
# Re-apply every boundary section of sql/01_schema.sql to a loaded database,
# the way an operator applies one to the live database before its service
# deploys (ADR-0040). Run from the repository root; each section is piped to
# the psql command given, which must read SQL on stdin:
#
#   .github/scripts/apply-boundaries.sh docker compose -f compose.distributed.yaml \
#     exec -T db psql -U postgres -d retail
#
# Used by nightly.yml and ops/verify/suites.sh.
set -euo pipefail

(($#)) || { echo "usage: $0 <psql command...>" >&2; exit 2; }
for b in $(sed -n 's/^-- BEGIN BOUNDARY \(.*\)$/\1/p' sql/01_schema.sql); do
  sed -n "/^-- BEGIN BOUNDARY $b\$/,/^-- END BOUNDARY $b\$/p" sql/01_schema.sql \
    | "$@" -X -q -v ON_ERROR_STOP=1 >/dev/null \
    || { echo "apply-boundaries: section $b failed" >&2; exit 1; }
done

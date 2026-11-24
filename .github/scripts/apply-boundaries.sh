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
#
# The callers wait for the db healthcheck, which passes only against the real
# server, not the image's temporary init server (#1224). As a safety net, a
# section whose connection was refused or cut, as by a server still starting
# or shutting down, is retried until APPLY_BOUNDARIES_DEADLINE seconds (default
# 120) have passed. The sections are idempotent, so a retry is safe. Any other
# error fails at once.
set -euo pipefail

(($#)) || { echo "usage: $0 <psql command...>" >&2; exit 2; }

deadline=$((SECONDS + ${APPLY_BOUNDARIES_DEADLINE:-120}))
transient='terminating connection due to administrator command|server closed the connection unexpectedly|connection to server was lost|connection to server .* failed|the database system is (starting up|shutting down|in recovery mode)'
err=$(mktemp)
trap 'rm -f "$err"' EXIT

for b in $(sed -n 's/^-- BEGIN BOUNDARY \(.*\)$/\1/p' sql/01_schema.sql); do
  until sed -n "/^-- BEGIN BOUNDARY $b\$/,/^-- END BOUNDARY $b\$/p" sql/01_schema.sql \
    | "$@" -X -q -v ON_ERROR_STOP=1 >/dev/null 2>"$err"; do
    cat "$err" >&2
    if ! grep -Eq "$transient" "$err" || ((SECONDS >= deadline)); then
      echo "apply-boundaries: section $b failed" >&2
      exit 1
    fi
    echo "apply-boundaries: section $b lost its connection; retrying" >&2
    sleep 2
  done
  cat "$err" >&2
done

#!/usr/bin/env bash
# ADR-0041 desktop compliance checks. Each prints nothing when it passes;
# the script exits 1 when any of them printed something.
set -u
cd "$(dirname "$0")/.."
out=$(
  grep -rn "application/json" src/ | sed 's/^/desktop requests JSON: /'
  grep -rnE "nodeIntegration:\s*true|contextIsolation:\s*false|sandbox:\s*false|webSecurity:\s*false" src/ |
    sed 's/^/insecure BrowserWindow: /'
  grep -rlE "safeStorage|refresh_token" src/ | grep -i renderer | sed 's/^/token handling in renderer: /'
  for p in $(grep -rhoE "/api/[a-z-]+/v[0-9]+" src/ | cut -d/ -f3 | sort -u); do
    grep -qE "^  - name: ${p}\$" ../../contracts/registry.yaml || echo "client calls unregistered service: $p"
  done
)
if [ -n "$out" ]; then
  echo "$out"
  exit 1
fi

#!/usr/bin/env bash
# ADR-0040's compose and NGINX loop, over every <dir>/*/ with a compose.yaml.
#
#   deploy/templates/service/check.sh [dir]          # dir defaults to services
#
# Prints nothing when each project is mosaiq-<name>, has no depends_on,
# publishes only 127.0.0.1:<port>, is hardened, and its NGINX include proxies
# to 127.0.0.1:<port> with X-Real-IP; otherwise one FAIL line per problem.
# <port> is the service's registry port; REGISTRY points at another registry,
# for the rendered test services in CI. Needs yq (the jq-syntax one CI pins).
set -uo pipefail
shopt -s nullglob

root=${1:-services}
REGISTRY=${REGISTRY:-contracts/registry.yaml}

for d in "$root"/*/; do
  d=${d%/}; n=$(basename "$d"); f="$d/compose.yaml"; c="$d/nginx/$n.conf"
  [ -f "$f" ] || continue
  port=$(yq -r ".services[] | select(.name == \"$n\") | .port" "$REGISTRY")
  [ -n "$port" ] || { echo "FAIL: $n has no port in $REGISTRY"; continue; }
  [ "$(yq -r '.name' "$f")" = "mosaiq-$n" ] || echo "FAIL: $f is not project mosaiq-$n"
  yq -e '.services[] | select(has("depends_on"))' "$f" >/dev/null 2>&1 && echo "FAIL: $f has depends_on"
  yq -r '.services[].ports[]?' "$f" | grep -v "^127\.0\.0\.1:$port:" | sed "s|^|FAIL: $f publishes |"
  yq -e '.services[] | select((.user // "0" | test("^(0|root)(:|$)")) or ((.cap_drop // []) | index("ALL") | not) or .read_only != true or ((.security_opt // []) | any(test("no-new-privileges")) | not))' "$f" >/dev/null 2>&1 && echo "FAIL: $f is not hardened"
  { grep -q "proxy_pass http://127\.0\.0\.1:$port/;" "$c" && grep -q 'X-Real-IP \$remote_addr' "$c"; } 2>/dev/null || echo "FAIL: $c"
done

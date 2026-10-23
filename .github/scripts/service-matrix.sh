#!/usr/bin/env bash
# The services a change touches, for CI's per-service matrix (ADR-0040, F13-09).
#
#   git diff --name-only <base>...HEAD | .github/scripts/service-matrix.sh
#   .github/scripts/service-matrix.sh summary '<matrix>' '<result>'
#
# The first form reads changed paths on stdin, one per line, and prints a JSON
# array of service names. Nothing here lists a service: a name is selected
# only when services/<name>/Dockerfile exists.
# - services/<name>/** or contracts/<name>/** selects <name>;
# - contracts/common/v1/**, tests/conformance/** and this workflow,
#   .github/workflows/services.yml, select every built service: a change to how
#   every service is tested is tested on every service;
# - a contract-only change for a service not built yet selects nothing.
#
# The second form is the required `services` check: it passes when the matrix
# is empty or the matrix job's result is success, and fails otherwise.
#
# ROOT (default .) is the repository root the Dockerfiles are looked up in.
set -euo pipefail

if [[ ${1:-} == summary ]]; then
  [[ $# -eq 3 ]] || { echo "usage: $0 summary '<matrix>' '<result>'" >&2; exit 2; }
  if [[ $2 == "[]" || $3 == success ]]; then
    exit 0
  fi
  echo "services: the matrix $2 finished as '$3'" >&2
  exit 1
fi

root=${ROOT:-.}
declare -A selected=()
all=0

built() { [[ -f $root/services/$1/Dockerfile ]]; }

while IFS= read -r path || [[ -n $path ]]; do
  case $path in
    contracts/common/v1/* | tests/conformance/* | .github/workflows/services.yml) all=1 ;;
    services/*/* | contracts/*/*)
      name=${path#*/}
      name=${name%%/*}
      if [[ $name =~ ^[a-z][a-z0-9-]*$ ]] && built "$name"; then
        selected[$name]=1
      fi
      ;;
  esac
done

if ((all)); then
  for dockerfile in "$root"/services/*/Dockerfile; do
    [[ -f $dockerfile ]] || continue
    selected[$(basename "$(dirname "$dockerfile")")]=1
  done
fi

json=
while IFS= read -r name; do
  [[ -n $name ]] && json+="${json:+,}\"$name\""
done < <(printf '%s\n' "${!selected[@]}" | sort)
printf '[%s]\n' "$json"
